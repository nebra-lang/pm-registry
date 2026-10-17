# pm-registry

The package registry of [Nebra](https://github.com/nebra-lang/nebra). It maps package names such as `@nebra-lang/nanos-world` to the public GitHub repositories they are installed from; versions are the repositories' git tags.

```bash
nebra add @nebra-lang/nanos-world@^1
```

## Publishing

From a project with a `nebra.toml` in a public GitHub repository, at its root or in one of its directories:

```bash
nebra publish                       # as @<your GitHub account>/<name from nebra.toml>
nebra publish --scope my-org        # as @my-org/<name>
nebra unpublish                     # remove the current project again
nebra scope add-owner my-org alice  # let alice publish to @my-org as well
```

Each command opens an issue in this repository through your GitHub login (`gh auth login`, `GITHUB_TOKEN` or `GH_TOKEN`); without one, it opens the prepared issue in your browser. A workflow checks the request, updates the registry and answers on the issue, one request at a time.

A request is accepted when the repository is public and the `nebra.toml` of the project, at the repository's root or in the directory it was published from, names the package, with or without its scope, and when the scope allows it:

- **you own the scope**, or it is named after your GitHub account, or after an organization you are a *public* member of; or
- **the repository belongs to the scope's account or organization and its `nebra.toml` names the package with the scope**, such as `name = "@my-org/core"`. Only those who can write to the organization's repositories can do that, so this works for private members too and gives nobody ownership of the scope.

The first package published to a scope nobody has claimed yet claims it: for you when the scope is named after your account or an organization you are a public member of, otherwise for its account or organization alone. A package whose repository is gone, private or no longer names it can be unpublished by anyone.

## Layout

| Path | Contents |
|---|---|
| `scopes/<scope>.json` | the GitHub accounts that own a scope |
| `packages/<scope>/<name>.json` | the repository of a package, the directory it is in, and its description |
| `aliases.json` | unscoped names kept for older projects, such as `nanos-world` → `@nebra-lang/nanos-world` |
| `index.json` | generated from the files above; the CLI reads only this |
| `scripts/registry.py` | the workflow's logic: checking requests and generating the index |

`index.json` is never edited by hand: every change to `packages/`, `scopes/` or `aliases.json` regenerates it.
