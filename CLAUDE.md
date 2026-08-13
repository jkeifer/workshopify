# workshopify

Scaffolding for template-rendered, notebook-based workshop repositories. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for setup, hooks, and release mechanics.

## Layering

The package is a src-layout library with one command-line surface.

| Module | Responsibility |
| --- | --- |
| `config.py` | The declarative half: parses `[tool.workshopify]` into frozen dataclasses, each subtable spelled once by the dataclass that receives it. Names the context module but never executes it. Owns the repo root, so it also owns the paths derived from it: `pyproject`, `display_path` for naming a file to a human, `within` for judging that a path is one the repo has and spelling it the repo's way, and `rebase` for placing that spelling in a generated tree. |
| `context.py` | Loads and executes the workshop's context module, yielding the namespace the cog generators run in and the declared `ASSETS` map. The module is optional: a workshop that names none gets the empty context, which every consumer handles as the nothing-to-do it is. |
| `render.py` | The cog render engine: rendering values in place, checking they are current, and stripping the templating for publication. |
| `assets.py` | The other half of `render` and `check`: the files `ASSETS` declares as carried verbatim rather than rendered, copied and byte-verified here. |
| `notebooks.py` | `src/*.py` to completed notebooks to exercise notebooks and notes, via jupytext and ipynb-scrubber. |
| `deps.py` | Derives the participants' pyproject and lock from the repo's own. |
| `build.py` | Assembles the published tree from every source of files, and publishes it into a worktree. |
| `worktree.py` | Owns the branch worktree a build publishes into: prepares it, and reports on or empties an already-prepared one. |
| `params.py` | Rewrites `[tool.workshopify.params]` values in pyproject.toml. |
| `git.py` | git's argv and cwd, over `proc.py`. Nothing else spells `git`. |
| `fs.py` | The filesystem boundary: every read, write, copy, and listing the package does. Owns "mode is the source's" and "text is UTF-8". |
| `proc.py` | The subprocess boundary: the one `subprocess.run`, for git and uv alike. |
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
  given boundary owns as a `WorkshopifyError`. Two of those boundaries are
  modules rather than a convention -- `fs.py` and `proc.py` -- so the wrapper
  is written once and reached through, not restated per call site. Nothing
  else may import `shutil` or `subprocess`: ruff's `banned-api` says so, which
  is what makes this half enforced. The rest is still convention, honestly:
  ruff cannot see a bare `Path.read_text`, so a new read that skips `fs.py`
  will lint clean and reach the user as a traceback. A third-party entry point
  owns its own exception type and hands it to `diagnosed` at the call site --
  cog's in `render._run`, tomlkit's and tomllib's where a pyproject is parsed,
  jupytext's config error in `notebooks.generate`, the scrubber's in its own
  `try`. That list is exhaustive on purpose: `jupytext.reads`/`writes` are
  **not** wrapped, because they run over text `render.publish` has already
  validated. Adding a call to one of them means deciding which it is. The one
  deliberate exception is a bug in this package rather than in a workshop: an
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
  and why `fs.py` -- through which every copy and every text file goes -- sets
  the mode from the source (`shutil.copy`, never `copyfile`, which inherits an
  existing destination's) and pins text to UTF-8 (so what a build produces
  does not depend on the locale of the machine that ran it).
- **A writing step reports what it wrote**, derived from what it actually
  wrote rather than from a glob of the output directory -- and the report is
  produced by the same traversal that does the writing, so the two cannot
  drift apart. The build subtracts that set from the published branch's
  tracked files to catch a renamed exercise still shipping its old file, so a
  file written but unreported is accused of being cruft on every build, and
  `--clean` can never resolve it. The report also carries *what* wrote each
  path, right up to the set it finally becomes, because a published path has
  exactly one source: an `ASSETS` target that is also an `include` entry is a
  config error, and a set of paths alone cannot tell it from a path written
  once.

## Known rough edges

- `__init__.py` is empty; the package has no curated public API yet.
