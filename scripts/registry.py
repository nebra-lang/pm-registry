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


def scope_owners(scope):
    return [owner.lower() for owner in read_json(SCOPES / f"{scope}.json", {}).get("owners", [])]


def require_owner(scope, author):
    owners = scope_owners(scope)
    if not owners:
        raise Rejected(f"The scope `@{scope}` does not exist yet; publishing a package to it claims it.")
    if author.lower() not in owners:
        raise Rejected(f"`{author}` is not an owner of `@{scope}`. Its owners are: {', '.join(owners)}.")


def require_owner_or_claim(github, scope, author):
    """Returns whether publishing to the scope claims it for the author, who may claim it as their
    own account or as an organization they are a public member of."""
    if (SCOPES / f"{scope}.json").exists():
        require_owner(scope, author)
        return False

    if scope == author.lower() or github.exists(f"/orgs/{scope}/public_members/{author}"):
        return True

    raise Rejected(
        f"`@{scope}` can only be claimed by the GitHub account `{scope}` or by a public member of the "
        f"organization `{scope}`. If you are a member, make your membership public and try again.")


def check_repository(github, repo, scope, name):
    match = REPO_PATTERN.match(repo or "")
    if not match:
        raise Rejected(f"`{repo}` is not a GitHub repository URL such as `https://github.com/owner/repo`.")

    owner, repository = match.group(1), match.group(2)
    status, info = github.get(f"/repos/{owner}/{repository}")
    if status != 200 or info is None:
        raise Rejected(f"The repository `{owner}/{repository}` does not exist or is not public.")
    if info.get("private"):
        raise Rejected(f"The repository `{owner}/{repository}` is private; packages have to be public.")

    status, manifest = github.get(f"/repos/{owner}/{repository}/contents/nebra.toml")
    if status != 200 or manifest is None:
        raise Rejected(f"`{owner}/{repository}` has no `nebra.toml` at its root.")

    text = base64.b64decode(manifest.get("content", "")).decode("utf-8", errors="replace")
    declared = re.search(r'^\s*name\s*=\s*"([^"]*)"', text, re.MULTILINE)
    if not declared or declared.group(1) not in (name, f"@{scope}/{name}"):
        found = declared.group(1) if declared else "nothing"
        raise Rejected(f"The `nebra.toml` of `{owner}/{repository}` names the package `{found}`, not `{name}`.")

    return f"https://github.com/{info['full_name']}"


def publish(github, request, author):
    scope, name = split_package(request.get("package"))
    package_file = PACKAGES / scope / f"{name}.json"
    claims_scope = require_owner_or_claim(github, scope, author)

    repo = check_repository(github, request.get("repo"), scope, name)
    entry = {"repo": repo}
    description = (request.get("description") or "").strip()
    if description:
        entry["description"] = description[:300]

    updated = package_file.exists()
    if claims_scope:
        write_json(SCOPES / f"{scope}.json", {"owners": [author]})
    write_json(package_file, entry)
    verb = "updated" if updated else "published"
    return f"`@{scope}/{name}` is {verb}: `nebra add @{scope}/{name}` installs it from {repo}."


def unpublish(request, author):
    scope, name = split_package(request.get("package"))
    package_file = PACKAGES / scope / f"{name}.json"
    require_owner(scope, author)
    if not package_file.exists():
        raise Rejected(f"`@{scope}/{name}` is not published.")

    package_file.unlink()
    return f"`@{scope}/{name}` is unpublished."


def change_owner(github, request, author, adding):
    scope = (request.get("scope") or "").lstrip("@").lower()
    user = (request.get("user") or "").strip()
    if not SCOPE_PATTERN.match(scope):
        raise Rejected(f"`{scope}` is not a valid scope.")
    require_owner(scope, author)

    owners = read_json(SCOPES / f"{scope}.json", {}).get("owners", [])
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
        if len(owners) == 1:
            raise Rejected(f"`{user}` is the last owner of `@{scope}`; a scope keeps at least one.")
        owners = [owner for owner in owners if owner.lower() != user.lower()]
        message = f"`{user}` no longer owns `@{scope}`."

    write_json(SCOPES / f"{scope}.json", {"owners": owners})
    return message


def build_index():
    """Writes index.json: every package by its scoped name, and every alias by the package it stands for."""
    index = {}
    for package_file in sorted(PACKAGES.glob("*/*.json")):
        entry = read_json(package_file, {})
        index[f"@{package_file.parent.name}/{package_file.stem}"] = entry["repo"]

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
            message = unpublish(request, author)
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
