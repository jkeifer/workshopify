# Contributing

workshopify is the scaffolding behind template-rendered, notebook-based
workshop repositories. A workshop repo declares what it wants in
`[tool.workshopify]` tables in its `pyproject.toml`; workshopify renders the
templated values, generates the notebooks and notes, and assembles the branch
participants clone. **Requires Python 3.14+.**

## Project setup

This project uses [uv](https://docs.astral.sh/uv) for project management. On a
Mac it can be installed with `brew install uv`. uv manages its own virtual
environment, so there is no separate venv step.

```commandline
uv sync
```

CI installs with `--locked`.

## Git hooks (prek)

Hooks are managed by [prek](https://github.com/j178/prek), configured in
`.pre-commit-config.yaml`. Install them so they run on every `git commit`:

```commandline
uv run prek install
```

Run them explicitly against everything:

```commandline
uv run prek run --all-files
```

The hooks cover `ruff` (lint + format), `ty` (type checking), and a set of
file-hygiene checks. CI runs exactly this set, so it is worth keeping green
locally. Every hook is `language: system`: the tools come from the lockfile,
and the hook runner never installs anything itself.

To commit something that does not pass the hooks:

```commandline
git commit --no-verify
```

## Tests

```commandline
uv run pytest
```

Tests build real git repositories in temporary directories and run the real
tooling over them; there is very little mocking. Warnings are errors.

## Releases

The version is derived from git tags by `hatch-vcs` — it is never written into
`pyproject.toml`. To release, add the release notes to `CHANGELOG.md`, tag the
commit, and publish a GitHub release; the release workflow builds the package
and uploads it to PyPI via trusted publishing.
