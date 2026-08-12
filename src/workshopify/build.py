"""Assemble the published workshop tree and publish it into a worktree.

The publish branch is a pure build artifact: every file on it is reproducible
from the source branch. This module assembles that tree -- repo files named by
`config.build.include`, the optional static/ overlay, the context's declared
assets, the generated notebooks and notes, and a derived pyproject/lock -- and
publishes it into the worktree, with a guard against clobbering unpublished
work and a cruft report for tracked files no build wrote.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from workshopify import deps, fs, notebooks, worktree
from workshopify.config import Config
from workshopify.context import Context
from workshopify.errors import WorkshopifyError
from workshopify.git import git


def _copy(source: Path, dest: Path) -> list[Path]:
    """Copy a file or directory tree, returning the files written.

    One traversal both copies and reports, so the two cannot disagree. Walking
    the source separately did disagree: `shutil.copytree` follows a symlinked
    directory and copies what is under it, while `rglob('*')` does not recurse
    into one, so everything under a `data/link -> ../shared` was written and
    never reported -- flagged as cruft by `unwritten()` on every build, and
    unclearable by --clean, since the next build writes it again and still
    says nothing.

    A glob of the destination would be wrong in the other direction: a rebuild
    merges into a worktree an earlier build wrote, so leftovers this build
    never wrote would be reported as written and hidden from that same cruft
    check.

    The leaf copy is `fs.copy` and the listing is `fs.glob`, which is where
    "mode is the source's" and "a failed listing is a diagnostic" are stated;
    what this function adds is only the traversal and the report. Both steps
    carry their own diagnostic, so there is nothing left here to wrap.
    """
    if not source.is_dir():
        fs.copy(source, dest)
        return [dest]
    return [
        written
        for child in fs.glob(source, '*')
        for written in _copy(child, dest / child.name)
    ]


def _include_source(config: Config, name: str) -> Path:
    """The repo file or directory one include entry names.

    An include entry is a single name used twice -- `root / name` to read and
    `staging / name` to write -- so a name that is not both relative and free
    of `..` breaks that pairing rather than the read. An absolute entry makes
    the two the same path, and the copy an uncaught SameFileError; PurePath
    normalizes nothing, so `../shared` is read and written outside the
    worktree, whereupon `relative_to(staging)` succeeds lexically and the
    build reports `../shared/...` as cruft no --clean can ever resolve. Both
    are config errors, named here while the entry as the author spelled it is
    still in hand.
    """
    entry = Path(name)
    if entry.is_absolute() or '..' in entry.parts:
        raise WorkshopifyError(
            f'error: include entry {name!r} must be a relative path inside '
            f'the repo root {config.root}',
        )
    source = config.root / entry
    if not source.exists():
        raise WorkshopifyError(f'error: include entry {name!r} does not exist')
    return source


def build(config: Config, ctx: Context, staging: Path) -> set[Path]:
    """Assemble the complete published tree. Returns paths relative to staging."""
    # No mkdir: every path into staging goes through `fs.write` or `fs.copy`,
    # both of which create the directory they land in.
    #
    # notebooks.generate resolves its output dir, so the paths it reports back
    # are resolved; match that here or relative_to below fails.
    staging = staging.resolve()
    written: list[Path] = []

    for name in config.build.include:
        written += _copy(_include_source(config, name), staging / name)

    static_dir = config.root / 'static'
    if static_dir.is_dir():
        for source in (p for p in fs.glob(static_dir, '**/*') if p.is_file()):
            written += _copy(source, staging / source.relative_to(static_dir))

    # context.load already refuses a target outside the root, so this is the
    # same question asked of a Context that came from somewhere else.
    for target, source in sorted(ctx.assets.items()):
        written += _copy(source, config.rebase(target, staging, 'asset target'))

    written += notebooks.generate(config, ctx, staging)

    written += deps.write_deps(config, staging)

    return {p.relative_to(staging) for p in written}


def unwritten(target: Path, written: set[Path]) -> list[Path]:
    """Tracked files this build did not write.

    Lives here rather than in `worktree.py` with the other git-shelling
    helpers because the question it answers is about the build: it is only
    meaningful against a build's output set, and only a build has one.

    A stale tracked file shows in `git status` as nothing at all, because it
    is unchanged -- so without this, a renamed exercise leaves its old file on
    the branch and it ships silently.

    -z because with the default core.quotePath a non-ASCII path comes back
    quoted and backslash-escaped ("sub/caf\\303\\251.txt"), which can never
    match a Path from build() -- it would be reported as cruft forever, and
    --clean could never resolve it.
    """
    tracked = {Path(name) for name in git(target, 'ls-files', '-z').split('\0') if name}
    return sorted(tracked - written)


@dataclass(frozen=True)
class Published:
    """What a publish produced, both halves relative to the worktree root.

    `written` is what the build wrote; `stale` is what it did not but the
    branch tracks anyway -- a renamed exercise still shipping its old file.
    The caller reports them, so the two arrive together rather than one being
    printed here and the other returned.
    """

    written: set[Path]
    stale: list[Path]


def publish_into(
    config: Config,
    ctx: Context,
    target: Path,
    *,
    clean_first: bool,
    overwrite_dirty: bool,
) -> Published:
    """The publish itself: guard, clean, build, then look for cruft.

    Reports every failure -- its own guard's, and those of the steps it calls
    -- as a WorkshopifyError. The CLI is the boundary that decides what one
    costs; this layer never exits, and never prints.
    """
    if worktree.is_dirty(target) and not overwrite_dirty:
        raise WorkshopifyError(
            f'error: {target} has uncommitted changes.\n'
            '  They may be an unpublished build, or notebook edits made in '
            'Jupyter that are not yet synced back to src/.\n'
            '  Commit them, or re-run with --overwrite-dirty: the build then '
            'overwrites\n'
            '  any dirty file it writes and leaves the rest, and --clean '
            'additionally\n'
            '  deletes every tracked file, modifications included.',
        )

    if clean_first:
        worktree.clean(target)

    written = build(config, ctx, target)
    return Published(written, unwritten(target, written))
