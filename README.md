# workshopify

Skeleton and scaffolding for template-rendered, notebook-based workshop
repositories.

A workshop repo declares everything mechanical in `[tool.workshopify]` tables
in its `pyproject.toml`, and workshopify does the rest:

| Command | Does |
| --- | --- |
| `workshopify render` | Re-render templated values and assets in place |
| `workshopify check` | Report stale rendered values or assets; exit 1 if any |
| `workshopify generate` | Generate the notebooks and notes from `src/` |
| `workshopify build` | Assemble the published workshop tree into its branch worktree |
| `workshopify set <key> <value>` | Set a `[tool.workshopify.params]` value and re-render |
| `workshopify worktree <branch>` | Prepare a branch worktree without building |

## Configuration

Every command finds its workshop by walking up from the working directory to
the nearest `pyproject.toml` with a `[tool.workshopify]` table. That file's
directory is the repo root, and every path below is relative to it. Keys are
kebab-case, and every one of them has a default, so a table with nothing to
say may be left out entirely.

### `[tool.workshopify]`

| Key | Default | Does |
| --- | --- | --- |
| `context` | none | Names the context module: the workshop's procedural half, whose module-level names become the namespace the templating runs in, and whose optional `ASSETS` dict maps a path in the published tree to the repo file copied there verbatim. Omit it for a workshop that templates nothing and declares no assets; a module that *is* named must exist. |
| `params` | `{}` | The workshop's own values, handed to the context module as `params` and rewritten in place by `workshopify set`. The keys are whatever that module reads. |

### `[tool.workshopify.render]`

| Key | Default | Does |
| --- | --- | --- |
| `files` | `[]` | Glob patterns naming the files `render` rewrites in place and `check` verifies. A pattern that matches nothing is an error — a typo'd glob would otherwise render nothing, forever, in silence — while declaring no patterns at all is a workshop that templates nothing. |

### `[tool.workshopify.notebooks]`

| Key | Default | Does |
| --- | --- | --- |
| `src` | `"src"` | The directory holding the py:percent sources. Which notebooks exist is decided by the `[[tool.ipynb-scrubber.files]]` entries; this only says where each one's source is read from, as `<src>/<stem>.py`. |

### `[tool.workshopify.build]`

| Key | Default | Does |
| --- | --- | --- |
| `branch` | `"workshop"` | The branch the published tree is assembled onto, in a worktree at `<root>/<branch>`. `build` writes that tree and prints how to review and push it; it commits nothing itself. |
| `include` | `[]` | Repo files and directories copied into the published tree at the same relative path. Entries must be relative and free of `..`. |
| `keep-tables` | `["project", "tool.uv"]` | Dotted paths kept in the participants' derived `pyproject.toml`; everything else is dropped. A path names its whole subtree. |
| `drop-keys` | `[]` | Dotted paths carved back out of what `keep-tables` kept — the workspace wiring under `[tool.uv]`, say, which participants have no checkout to point at. |

A path in either list that matches nothing in the repo's own `pyproject.toml`
is an error rather than a no-op, as is a path listed alongside one of its own
descendants: both would otherwise ship a silently wrong pyproject.

### What `[tool.workshopify]` does not say

Some of the published tree comes from places no key names:

- `static/` at the repo root is an overlay — every file under it is copied to
  the same path in the published tree, with the `static/` prefix stripped.
- `[tool.jupytext]` and `[tool.ipynb-scrubber]` are read as those tools define
  them, so the notebook formats, the scrub markers, and the set of generated
  notebooks and notes are configured there and not here.
- `uv.lock` is derived from the repo's own lock, re-resolved offline, so
  participants get the versions contributors develop against — and a repo that
  has never run `uv lock` has nothing to build from.

## Status

Extracted from a working workshop repository and being generalised. The
configuration contract and command surface are still subject to change.
