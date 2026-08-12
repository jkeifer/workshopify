"""Assemble the published workshop tree and publish it into a worktree.

The publish branch is a pure build artifact: every file on it is reproducible
from the source branch. This module assembles that tree -- repo files named by
`config.build.include`, the optional static/ overlay, the context's declared
assets, the generated notebooks and notes, and a derived pyproject/lock -- and
publishes it into the worktree, with a guard against clobbering unpublished
work and a cruft report for tracked files no build wrote.
"""

from __future__ import annotations

import shutil

from dataclasses import dataclass
from pathlib import Path

from workshopify import deps, notebooks, worktree
from workshopify.config import Config
from workshopify.context import Context
from workshopify.errors import WorkshopifyError
from workshopify.git import git


def _copy(source: Path, dest: Path) -> list[Path]:
    """Copy a file or directory tree, returning the files written.

    The return value is derived from the source, never from a glob of the
    destination. copytree(dirs_exist_ok=True) merges rather than replaces,
    so on a rebuild into a reused worktree a destination glob would report
    files this build never wrote -- hiding stale leftovers from the caller's
    cruft check instead of flagging them.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, dest, dirs_exist_ok=True)
        return [
            dest / p.relative_to(source)
            for p in sorted(source.rglob('*'))
            if p.is_file()
        ]
    shutil.copyfile(source, dest)
    return [dest]


def _rebase_asset(target: Path, root: Path, staging: Path) -> Path:
    """Where an asset target lands under staging.

    Asset targets are absolute paths under the repo root; rebasing them under
    staging means relative_to(root). A target outside root is a config error,
    diagnosed here rather than letting relative_to's bare ValueError escape.
    """
    try:
        return staging / target.relative_to(root)
    except ValueError as e:
        raise WorkshopifyError(
            f'error: asset target {target} is outside the repo root {root}',
        ) from e


def build(config: Config, ctx: Context, staging: Path) -> set[Path]:
    """Assemble the complete published tree. Returns paths relative to staging."""
    staging.mkdir(parents=True, exist_ok=True)
    # notebooks.generate resolves its output dir, so the paths it reports back
    # are resolved; match that here or relative_to below fails.
    staging = staging.resolve()
    written: list[Path] = []

    for name in config.build.include:
        source = config.root / name
        if not source.exists():
            raise WorkshopifyError(f'error: include entry {name!r} does not exist')
        written += _copy(source, staging / name)

    static_dir = config.root / 'static'
    if static_dir.is_dir():
        for source in sorted(p for p in static_dir.rglob('*') if p.is_file()):
            written += _copy(source, staging / source.relative_to(static_dir))

    for target, source in sorted(ctx.assets.items()):
        written += _copy(source, _rebase_asset(target, config.root, staging))

    written += notebooks.generate(config, ctx, staging)

    deps.write_deps(config, staging)
    written += [staging / 'pyproject.toml', staging / 'uv.lock']

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
