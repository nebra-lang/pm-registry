#!/usr/bin/env python3
"""Processes requests to the Nebra package registry and regenerates its index.

A request is an issue whose body carries a JSON block after the marker
``<!-- nebra-registry-request -->``. The issue's author is who the request is
made by, as GitHub authenticated them.
"""

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("REGISTRY_ROOT") or Path(__file__).resolve().parent.parent)
PACKAGES = ROOT / "packages"
SCOPES = ROOT / "scopes"
ALIASES = ROOT / "aliases.json"
INDEX = ROOT / "index.json"

MARKER = "<!-- nebra-registry-request -->"
SCOPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}$")
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
PACKAGE_PATTERN = re.compile(r"^@([^/]+)/(.+)$")
SUBDIR_PATTERN = re.compile(r"^(?!.*(?:^|/)\.\.?(?:/|$))[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
REPO_PATTERN = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")


class Rejected(Exception):
    """A request that cannot be carried out, with the reason shown to its author."""


class GitHub:
    def __init__(self, token):
        self.token = token

    def get(self, path):
        request = urllib.request.Request("https://api.github.com" + path)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("User-Agent", "nebra-registry")
        if self.token:
            request.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(request) as response:
                body = response.read()
                return response.status, json.loads(body) if body else None
        except urllib.error.HTTPError as error:
            return error.code, None

    def exists(self, path):
        return self.get(path)[0] in (200, 204)


def read_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=4, sort_keys=True) + "\n", encoding="utf-8")


def split_package(package):
    match = PACKAGE_PATTERN.match(package or "")
    if not match:
        raise Rejected(f"`{package}` is not a scoped package name such as `@scope/name`.")

    scope, name = match.group(1), match.group(2)
    if not SCOPE_PATTERN.match(scope):
        raise Rejected(f"`{scope}` is not a valid scope: use lowercase letters, digits and dashes.")
    if not NAME_PATTERN.match(name):
        raise Rejected(f"`{name}` is not a valid package name: use lowercase letters, digits, `.`, `_` and `-`.")
    return scope, name


def read_scope(scope):
    return read_json(SCOPES / f"{scope}.json", None)


def is_owner(scope, author):
    owners = (read_scope(scope) or {}).get("owners", [])
    return author.lower() in [owner.lower() for owner in owners]


def is_member(github, scope, author):
    """Whether the author is the account the scope is named after or a public member of the
    organization it is named after. Private memberships cannot be seen by the registry."""
    return scope == author.lower() or github.exists(f"/orgs/{scope}/public_members/{author}")


def require_authority(github, scope, author):
    if is_owner(scope, author) or is_member(github, scope, author):
        return
    raise Rejected(
        f"`{author}` cannot change `@{scope}`: only its owners, the account `{scope}` and public members "
        f"of the organization `{scope}` can.")


def check_repository(github, repo, subdir, scope, name):
    """Checks that a package's repository is public and declares the package, and returns its
    canonical URL and whether it proves the scope: a repository owned by the scope's account or
    organization that names the package with its scope, which only those who can write to it can."""
    match = REPO_PATTERN.match(repo or "")
    if not match:
        raise Rejected(f"`{repo}` is not a GitHub repository URL such as `https://github.com/owner/repo`.")
    if subdir and not SUBDIR_PATTERN.match(subdir):
        raise Rejected(f"`{subdir}` is not a directory of the repository such as `packages/core`.")

    owner, repository = match.group(1), match.group(2)
    status, info = github.get(f"/repos/{owner}/{repository}")
    if status != 200 or info is None:
        raise Rejected(f"The repository `{owner}/{repository}` does not exist or is not public.")
    if info.get("private"):
        raise Rejected(f"The repository `{owner}/{repository}` is private; packages have to be public.")

    location = f"{subdir}/nebra.toml" if subdir else "nebra.toml"
    declared = declared_name(github, info["full_name"], location)
    if declared is None:
        raise Rejected(f"`{owner}/{repository}` has no `{location}`.")
    if declared not in (name, f"@{scope}/{name}"):
        raise Rejected(f"The `{location}` of `{owner}/{repository}` names the package `{declared}`, not `{name}`.")

    proves_scope = info["owner"]["login"].lower() == scope and declared == f"@{scope}/{name}"
    return f"https://github.com/{info['full_name']}", proves_scope


def declared_name(github, full_name, location):
    status, manifest = github.get(f"/repos/{full_name}/contents/{location}")
    if status != 200 or manifest is None:
        return None

    text = base64.b64decode(manifest.get("content", "")).decode("utf-8", errors="replace")
    declared = re.search(r'^\s*name\s*=\s*"([^"]*)"', text, re.MULTILINE)
    return declared.group(1) if declared else ""


def publish(github, request, author):
    scope, name = split_package(request.get("package"))
    package_file = PACKAGES / scope / f"{name}.json"
    subdir = (request.get("subdir") or "").strip("/")
    repo, proves_scope = check_repository(github, request.get("repo"), subdir, scope, name)

    existing = read_scope(scope)
    if not (proves_scope or is_owner(scope, author) or is_member(github, scope, author)):
        raise Rejected(
            f"`{author}` cannot publish to `@{scope}`. Publish from your own account or an organization you are "
            f"a public member of, or from a repository of `{scope}` whose `nebra.toml` names the package "
            f"`@{scope}/{name}`, which proves the organization wants it published.")

    entry = {"repo": repo}
    if subdir:
        entry["subdir"] = subdir
    description = (request.get("description") or "").strip()
    if description:
        entry["description"] = description[:300]

    if existing is None:
        claimed = {"owners": [author]} if is_member(github, scope, author) else {"owners": []}
        claimed["account"] = scope
        write_json(SCOPES / f"{scope}.json", claimed)

    verb = "updated" if package_file.exists() else "published"
    write_json(package_file, entry)
    return f"`@{scope}/{name}` is {verb}: `nebra add @{scope}/{name}` installs it from {repo}."


def is_abandoned(github, entry, scope, name):
    """Whether a published package no longer resolves: its repository is gone or private, or no
    longer declares the package. Anyone may remove such an entry."""
    match = REPO_PATTERN.match(entry.get("repo", ""))
    if not match:
        return True

    status, info = github.get(f"/repos/{match.group(1)}/{match.group(2)}")
    if status != 200 or info is None or info.get("private"):
        return True

    subdir = entry.get("subdir")
    declared = declared_name(github, info["full_name"], f"{subdir}/nebra.toml" if subdir else "nebra.toml")
    return declared not in (name, f"@{scope}/{name}")


def unpublish(github, request, author):
    scope, name = split_package(request.get("package"))
    package_file = PACKAGES / scope / f"{name}.json"
    if not package_file.exists():
        raise Rejected(f"`@{scope}/{name}` is not published.")

    if not is_abandoned(github, read_json(package_file, {}), scope, name):
        require_authority(github, scope, author)

    package_file.unlink()
    return f"`@{scope}/{name}` is unpublished."


def change_owner(github, request, author, adding):
    scope = (request.get("scope") or "").lstrip("@").lower()
    user = (request.get("user") or "").strip()
    if not SCOPE_PATTERN.match(scope):
        raise Rejected(f"`{scope}` is not a valid scope.")

    existing = read_scope(scope)
    if existing is None:
        raise Rejected(f"The scope `@{scope}` does not exist yet; publishing a package to it claims it.")
    require_authority(github, scope, author)

    owners = existing.get("owners", [])
    present = [owner for owner in owners if owner.lower() == user.lower()]
    if adding:
        if present:
            raise Rejected(f"`{user}` already owns `@{scope}`.")
        if not github.exists(f"/users/{user}"):
            raise Rejected(f"There is no GitHub account `{user}`.")
        owners.append(user)
        message = f"`{user}` now owns `@{scope}`."
    else:
        if not present:
            raise Rejected(f"`{user}` does not own `@{scope}`.")
        owners = [owner for owner in owners if owner.lower() != user.lower()]
        message = f"`{user}` no longer owns `@{scope}`."

    existing["owners"] = owners
    write_json(SCOPES / f"{scope}.json", existing)
    return message


def build_index():
    """Writes index.json: every package by its scoped name, and every alias by the package it stands for.
    A package in a directory of its repository is written as `github:owner/repo/directory`."""
    index = {}
    for package_file in sorted(PACKAGES.glob("*/*.json")):
        entry = read_json(package_file, {})
        location = entry["repo"]
        if entry.get("subdir"):
            location = "github:" + location.removeprefix("https://github.com/") + "/" + entry["subdir"]
        index[f"@{package_file.parent.name}/{package_file.stem}"] = location

    for alias, package in read_json(ALIASES, {}).items():
        if package in index:
            index[alias] = index[package]

    write_json(INDEX, index)


def read_request(body):
    if MARKER not in (body or ""):
        raise Rejected("The issue is not a registry request.")

    block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", body.split(MARKER, 1)[1], re.DOTALL)
    if not block:
        raise Rejected("The request has no JSON block after its marker.")
    try:
        return json.loads(block.group(1))
    except json.JSONDecodeError as error:
        raise Rejected(f"The request's JSON cannot be read: {error.msg}.")


def process(event_path, result_path):
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    issue = event["issue"]
    author = issue["user"]["login"]
    github = GitHub(os.environ.get("GITHUB_TOKEN"))

    try:
        request = read_request(issue.get("body"))
        action = request.get("action")
        if action == "publish":
            message = publish(github, request, author)
        elif action == "unpublish":
            message = unpublish(github, request, author)
        elif action in ("add-owner", "remove-owner"):
            message = change_owner(github, request, author, adding=action == "add-owner")
        else:
            raise Rejected(f"`{action}` is not a registry action.")

        build_index()
        result = {"accepted": True, "message": message}
    except Rejected as rejection:
        result = {"accepted": False, "message": str(rejection)}

    Path(result_path).write_text(json.dumps(result), encoding="utf-8")
    print(json.dumps(result))


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "index":
        build_index()
    elif command == "process" and len(sys.argv) >= 4:
        process(sys.argv[2], sys.argv[3])
    else:
        print("usage: registry.py index | registry.py process <event.json> <result.json>", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
