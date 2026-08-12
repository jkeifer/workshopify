# workshopify

Scaffolding for template-rendered, notebook-based workshop repositories. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for setup, hooks, and release mechanics.

## Layering

The package is a src-layout library with one command-line surface.

| Module | Responsibility |
| --- | --- |
| `config.py` | The declarative half: parses `[tool.workshopify]` into frozen dataclasses. Names the context module but never executes it. Owns the repo root, so it also owns the paths derived from it: `pyproject`, and `display_path` for naming a file to a human. |
| `context.py` | Loads and executes the workshop's context module, yielding the namespace the cog generators run in and the declared `ASSETS` map. |
| `render.py` | The cog render engine: rendering values in place, checking they are current, and stripping the templating for publication. |
| `notebooks.py` | `src/*.py` to completed notebooks to exercise notebooks and notes, via jupytext and ipynb-scrubber. |
| `deps.py` | Derives the participants' pyproject and lock from the repo's own. |
| `build.py` | Assembles the published tree from every source of files, and publishes it into a worktree. |
| `worktree.py` | Owns the branch worktree a build publishes into: prepares it, and reports on or empties an already-prepared one. |
| `params.py` | Rewrites `[tool.workshopify.params]` values in pyproject.toml. |
| `git.py` | The single git subprocess helper. |
| `errors.py` | `WorkshopifyError`. |
| `cli.py` | The only place a failure becomes a process exit. |

## Invariants

- **Nothing below `cli.py` exits the process.** Every module raises
  `WorkshopifyError` carrying the full diagnostic; `cli.py` prints it to stderr
  and returns 1. Do not add `sys.exit` anywhere else.
- **Nothing below `cli.py` writes to stdout or stderr.** Every module returns
  what it computed -- paths written, stale files, advisory notes -- and
  `cli.py` decides what the user sees, in what order, and in what form. Do not
  add `print` anywhere else; `cli.py` holds the only `T201` ignore, so ruff
  enforces this.
- **Generated output is a pure function of its source.** Notebook cell ids are
  positional, not random, so re-rendering an unchanged source produces a
  byte-identical file and a publish diff contains only real changes.
- **`generate()` reports the files it wrote**, derived from what the writing
  steps returned rather than from a glob of the output directory. The build
  subtracts that set from the published branch's tracked files to catch a
  renamed exercise still shipping its old file.

## Known rough edges

- `__init__.py` is empty; the package has no curated public API yet.
