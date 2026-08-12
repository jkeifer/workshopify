"""Generate the completed + exercise notebooks (and notes) from the src/ files.

The py:percent files in the notebooks source dir are the source of truth. For
each one, as a single notebook object from the first read to the last write:

  1. Strip the embedded cog templating (render.publish), which also validates
     that the stripped text is a structurally sound py:percent document.
  2. Read it as a notebook via the jupytext API and give every cell an id
     derived from its position, then write the completed .ipynb under
     notebooks/completed/.
  3. Hand that same object to ipynb-scrubber, which returns the exercise
     notebook (notebooks/NN_<name>.ipynb) in the class it arrived as, plus the
     rendered notes document (notes/NN_<name>.md) when a cell was noted.

jupytext serializes every .ipynb written here, the completed one and the
scrubbed one alike, so the two agree on indentation and escaping without this
module restating either -- and nothing is parsed back out of JSON in between.

All steps write into output_dir, which is the directory that *contains* the
`notebooks/` and `notes/` subdirectories. Point it at any directory to stage
the generated files there.

Filenames, markers, and clear-text are read from the [tool.ipynb-scrubber]
config in pyproject.toml, so this stays single-sourced with the local
`scrub-project` workflow. We drive the scrubber through its Python API with
each config entry's paths rebased under output_dir, which is what
`scrub-project` itself does internally -- so the output is identical, without a
rewritten temp config.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import jupytext

from ipynb_scrubber import FileEntry, ProjectConfig, ScrubberError, scrub_parsed
from jupytext.config import load_jupytext_configuration_file

from workshopify.config import Config
from workshopify.context import Context
from workshopify.errors import WorkshopifyError
from workshopify.render import publish


def _rebase(entry: FileEntry, root: Path, output_dir: Path) -> FileEntry:
    """Repoint one config entry's paths at output_dir.

    The scrubber resolves what its config names against the directory it read
    that config from, so the paths arriving here are absolute and point into
    the repo; output_dir may be a worktree. Joining an absolute path discards
    output_dir entirely, so each is made relative to the root first -- without
    that, a build writes its generated notebooks over the source tree and
    reports success, which is the whole of the symptom.

    Both sides are resolved before the subtraction because one directory has
    more than one spelling: on macOS a path under /var and the same path under
    /private/var name the same place.
    """
    root = root.resolve()

    def under(path: Path) -> Path:
        try:
            return output_dir / path.resolve().relative_to(root)
        except ValueError as e:
            raise WorkshopifyError(
                f'error: the [[tool.ipynb-scrubber.files]] entry for '
                f'{entry.input} names {path}, which is outside the repo at '
                f'{root}',
            ) from e

    return replace(
        entry,
        input=under(entry.input),
        output=under(entry.output),
        notes_file=under(entry.notes_file) if entry.notes_file else None,
    )


def _write(path: Path, text: str) -> None:
    """Write `text` to `path` verbatim, creating the directory it lands in.

    Nothing is appended, because this serves both kinds of output and only one
    of them needs it: `jupytext.writes` omits the trailing newline, so its two
    callers add one, while the rendered notes document already terminates
    itself and would gain a blank line.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _render(config: Config, ctx: Context, entry: FileEntry) -> Path | None:
    """src/<stem>.py -> the completed and exercise notebooks, and any notes.

    Returns the notes file written, or None. This is the code that decides
    whether there is one -- a configured notes-file is only written when the
    notebook has noted cells -- so it is the only place that can answer
    without re-deriving the answer from the filesystem afterwards. Returning
    the path rather than a bool keeps the "notes implies a notes_file"
    invariant checkable where it is established, instead of leaving the caller
    to re-assert it.
    """
    stem = entry.input.stem
    src_py = config.root / config.notebooks.src / f'{stem}.py'
    if not src_py.exists():
        raise WorkshopifyError(f'error: missing source file {src_py}')

    published = publish(ctx, src_py.read_text(), str(src_py))
    # The [tool.jupytext] config must reach the API calls explicitly; only the
    # CLI discovers it. Without it the cell metadata filter that keeps
    # `raises-exception` tags falls back to defaults.
    jt_config = load_jupytext_configuration_file(str(config.pyproject))
    notebook = jupytext.reads(published, fmt='py:percent', config=jt_config)
    # Cell ids are derived from the notebook stem and cell index. nbformat
    # mints a random uuid4 per cell, so without this a plain render rewrites
    # every cell of every notebook and a publish diff is pure churn;
    # positional ids make the render a pure function of its source, with
    # nothing to merge into.
    for index, cell in enumerate(notebook['cells']):
        cell['id'] = f'{stem}-{index:03d}'

    # entry.input is the completed notebook: workshopify generates what the
    # scrubber then consumes. It is written first because the scrub result
    # shallow-shares every node it left alone with this notebook, so the two
    # must not be built in an order that lets one reach into the other.
    _write(entry.input, jupytext.writes(notebook, fmt='ipynb', config=jt_config) + '\n')

    # The scrubber reports a bad marker against the cell it sits in, which is
    # the one thing this can fail on that the author can fix. Naming the .py
    # they wrote rather than the .ipynb generated from it points at the file
    # they would edit; the cell index is the same in both.
    try:
        result = scrub_parsed(notebook, entry.options)
    except ScrubberError as e:
        raise WorkshopifyError(f'error: {src_py}: {e}') from e

    _write(
        entry.output,
        jupytext.writes(result.notebook, fmt='ipynb', config=jt_config) + '\n',
    )

    if result.notes_text is None:
        return None
    if entry.notes_file is None:
        raise WorkshopifyError(
            f'error: {entry.input} has {result.note_count} cell(s) marked '
            f'"{entry.options.note_tag}" but no notes-file is configured',
        )
    _write(entry.notes_file, result.notes_text)
    return entry.notes_file


def generate(config: Config, ctx: Context, output_dir: Path) -> list[Path]:
    """Render every configured notebook into output_dir.

    Returns the absolute paths written, reported by the steps that wrote them
    rather than by globbing output_dir. The build subtracts this from the
    published branch's tracked files to flag cruft, and output_dir may be a
    worktree a previous build already wrote into: a glob would count those
    leftovers as written, so a renamed exercise would keep shipping its old
    file, silently. In the other direction, a notes file is only written when
    the notebook has noted cells -- a notebook may declare one and produce
    none -- so the config alone would name a file that does not exist.
    """
    output_dir = output_dir.resolve()
    try:
        project = ProjectConfig.from_file(config.pyproject)
    except ScrubberError as e:
        raise WorkshopifyError(f'error: {e}') from e

    written: list[Path] = []
    for configured in project.files:
        entry = _rebase(configured, config.root, output_dir)
        notes_file = _render(config, ctx, entry)
        # The completed and exercise notebooks are written unconditionally.
        written += [entry.input, entry.output]
        if notes_file is not None:
            written.append(notes_file)
    return written
