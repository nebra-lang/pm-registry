# pm-registry

The package registry of [Nebra](https://github.com/nebra-lang/nebra). It maps package names such as `@nebra-lang/nanos-world` to the public GitHub repositories they are installed from; versions are the repositories' git tags.

```bash
nebra add @nebra-lang/nanos-world@^1
```

## Publishing

From the root of a public GitHub repository with a `nebra.toml`:

```bash
nebra publish                       # as @<your GitHub account>/<name from nebra.toml>
nebra publish --scope my-org        # as @my-org/<name>
nebra unpublish                     # remove the current project again
nebra scope add-owner my-org alice  # let alice publish to @my-org as well
```

Each command opens an issue in this repository through your GitHub login (`gh auth login`, `GITHUB_TOKEN` or `GH_TOKEN`); without one, it opens the prepared issue in your browser. A workflow checks the request, updates the registry and answers on the issue, one request at a time.

A request is accepted when:

- **the scope is yours**: you own it, or it is still unclaimed and named after your GitHub account or an organization you are a *public* member of. Publishing to an unclaimed scope claims it.
- **the repository is public** and has a `nebra.toml` at its root whose `name` is the package's name, with or without its scope.

## Layout

| Path | Contents |
|---|---|
| `scopes/<scope>.json` | the GitHub accounts that own a scope |
| `packages/<scope>/<name>.json` | the repository of a package and its description |
| `aliases.json` | unscoped names kept for older projects, such as `nanos-world` → `@nebra-lang/nanos-world` |
| `index.json` | generated from the files above; the CLI reads only this |
| `scripts/registry.py` | the workflow's logic: checking requests and generating the index |

`index.json` is never edited by hand: every change to `packages/`, `scopes/` or `aliases.json` regenerates it.
