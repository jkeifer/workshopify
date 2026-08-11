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

## Status

Extracted from a working workshop repository and being generalised. The
configuration contract and command surface are not yet documented in full and
are still subject to change.
