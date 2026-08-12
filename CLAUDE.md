# workshopify

Scaffolding for template-rendered, notebook-based workshop repositories. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for setup, hooks, and release mechanics.

## Layering

The package is a src-layout library with one command-line surface.

| Module | Responsibility |
| --- | --- |
| `config.py` | The declarative half: parses `[tool.workshopify]` into frozen dataclasses, each subtable spelled once by the dataclass that receives it. Names the context module but never executes it. Owns the repo root, so it also owns the paths derived from it: `pyproject`, `display_path` for naming a file to a human, and `rebase` for placing a repo path in a generated tree. |
| `context.py` | Loads and executes the workshop's context module, yielding the namespace the cog generators run in and the declared `ASSETS` map. |
| `render.py` | The cog render engine: rendering values in place, checking they are current, and stripping the templating for publication. |
| `notebooks.py` | `src/*.py` to completed notebooks to exercise notebooks and notes, via jupytext and ipynb-scrubber. |
| `deps.py` | Derives the participants' pyproject and lock from the repo's own. |
| `build.py` | Assembles the published tree from every source of files, and publishes it into a worktree. |
| `worktree.py` | Owns the branch worktree a build publishes into: prepares it, and reports on or empties an already-prepared one. |
| `params.py` | Rewrites `[tool.workshopify.params]` values in pyproject.toml. |
| `git.py` | The single git subprocess helper. |
| `errors.py` | `WorkshopifyError`, and `diagnosed` -- the boundary that turns a foreign failure into one. |
| `cli.py` | The only place a failure becomes a process exit. |

## Invariants

- **Nothing below `cli.py` exits the process.** Every module raises
  `WorkshopifyError` carrying the full diagnostic; `cli.py` prints it to stderr
  and returns 1. Do not add `sys.exit` anywhere else.
- **Every foreign failure is diagnosed at its boundary.** The invariant above
  only holds where the package meets code that does not know it exists: the
  filesystem, subprocesses, cog. Those calls go through `errors.diagnosed`,
  which restates `OSError`, `subprocess.SubprocessError`, and whatever else a
  given boundary owns as a `WorkshopifyError`. A new `subprocess.run`, `open`,
  `copy`, or third-party entry point wants that wrapper; without it a missing
  lockfile or a typo'd generator reaches the user as a traceback. The one
  exception is a bug in this package rather than in a workshop -- an
  unreadable config annotation raises `TypeError`, because no author can
  provoke it and none can fix it.
- **Nothing below `cli.py` writes to stdout or stderr.** Every module returns
  what it computed -- paths written, stale files, advisory notes -- and
  `cli.py` decides what the user sees, in what order, and in what form. Do not
  add `print` anywhere else; `cli.py` holds the only `T201` ignore, so ruff
  enforces this. Ruff cannot see a *library* writing on the package's behalf,
  so cog's output channels are bound away from the process streams in
  `render._run`; `workshopify worktree` prints its path to stdout as the
  command's result, and a chatty generator would otherwise corrupt it.
- **Generated output is a pure function of its source.** Notebook cell ids are
  positional, not random, so re-rendering an unchanged source produces a
  byte-identical file and a publish diff contains only real changes. This is
  why the cog namespace is deep-copied per file (a generator that mutates a
  context value must not change what the next file, or the next run, renders)
  and why every copy sets the source's mode (`shutil.copy`, never
  `copyfile`, which inherits an existing destination's).
- **A writing step reports what it wrote**, derived from what it actually
  wrote rather than from a glob of the output directory -- and the report is
  produced by the same traversal that does the writing, so the two cannot
  drift apart. The build subtracts that set from the published branch's
  tracked files to catch a renamed exercise still shipping its old file, so a
  file written but unreported is accused of being cruft on every build, and
  `--clean` can never resolve it.

## Known rough edges

- `__init__.py` is empty; the package has no curated public API yet.
