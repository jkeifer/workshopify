"""Assemble the published workshop tree and publish it into a worktree.

The publish branch is a pure build artifact: every file on it is reproducible
from the source branch. This module assembles that tree -- repo files named by
`config.build.include`, the optional static/ overlay, the context's declared
assets, the generated notebooks and notes, and a derived pyproject/lock -- and
publishes it into the worktree, with a guard against clobbering unpublished
work and a cruft report for tracked files no build wrote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from workshopify import deps, fs, notebooks, worktree
from workshopify.config import Config
from workshopify.context import Context
from workshopify.errors import WorkshopifyError
from workshopify.git import git


def _pairs(source: Path, dest: Path) -> list[tuple[Path, Path]]:
    """(source, dest) for every file copying `source` onto `dest` would write.

    The plan and the copies made from it come out of one traversal, so what a
    build reports and what it wrote cannot disagree. Walking the source
    separately from copying it can: `shutil.copytree` descends into a
    symlinked directory and `rglob('*')` does not, so everything under a
    `data/link -> ../shared` is written by one and unknown to the other. This
    walk is the copy's own and follows what the copy follows.

    Deriving the report from the destination instead goes wrong in the other
    direction: a rebuild merges into a worktree an earlier build wrote, so
    leftovers this build never wrote would be reported as written -- and
    hidden from the cruft check that exists to find exactly those.

    The listing is `fs.glob`, which is where a failed one becomes a
    diagnostic; nothing else here touches the filesystem, because nothing here
    writes.
    """
    if not source.is_dir():
        return [(source, dest)]
    return [
        pair
        for child in fs.glob(source, '*')
        for pair in _pairs(child, dest / child.name)
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


@dataclass
class _Tree:
    """The published tree being assembled: every path, and what puts it there.

    A published path has one source. What identifies a source is where the
    bytes come from, not how a diagnostic names it -- two include entries that
    overlap, or one named twice, write the same file from the same place, and
    are the config being repetitive rather than contradictory. The label is
    carried alongside because reconciling the two spellings is the fix and
    neither one alone locates it.

    Copies are planned and made in separate steps, so a collision is refused
    while the tree is still untouched, as every other config error the
    assembly can raise already is. `publish_into` hands in the user's own
    worktree as staging: a refusal that had written most of a tree first
    leaves them a directory to reason about instead of a message to act on.
    """

    staging: Path
    sources: dict[Path, tuple[Path | str, str]] = field(default_factory=dict)
    copies: dict[Path, Path] = field(default_factory=dict)

    def _place(self, dest: Path, origin: Path | str, label: str) -> None:
        relative = dest.relative_to(self.staging)
        rival, rival_label = self.sources.setdefault(relative, (origin, label))
        if rival != origin:
            raise WorkshopifyError(
                f'error: {rival_label} and {label} both write {relative} to '
                f'the published tree; whichever runs second is what ships, so '
                f'this build has no one answer. Rename or drop one of them.',
            )

    def to_copy(self, pairs: list[tuple[Path, Path]], label: str) -> None:
        """Plan these copies. Nothing is written until `copy_all`.

        Keyed by destination, which is the rule `_place` has just enforced: a
        repetition it allowed through -- one entry named twice, or a directory
        entry overlapping a file in it -- is one copy planned, not two made of
        the same bytes. A dict rather than a set because `copy_all` iterates
        this, and a set's order is its hashes': the same build would copy in a
        different order on every run, and one that failed partway would leave
        a different half of the tree behind each time.
        """
        for source, dest in pairs:
            self._place(dest, source, label)
            self.copies[dest] = source

    def copy_all(self) -> None:
        """Make every planned copy, in the order planned. Mode is the source's."""
        for dest, source in self.copies.items():
            fs.copy(source, dest)

    def generated(self, paths: list[Path], label: str) -> None:
        """Record files a generating step has already written.

        A generated file's origin is the step that wrote it: there is no
        source path for another step to have in common with it, so the label
        identifies it as well as names it and is deliberately passed as both.
        """
        for path in paths:
            self._place(path, label, label)

    @property
    def targets(self) -> set[Path]:
        return set(self.sources)


def build(config: Config, ctx: Context, staging: Path) -> set[Path]:
    """Assemble the complete published tree. Returns paths relative to staging."""
    # No mkdir: every path into staging goes through `fs.write` or `fs.copy`,
    # both of which create the directory they land in.
    #
    # notebooks.generate resolves its output dir, so the paths it reports back
    # are resolved; match that here or relative_to below fails.
    staging = staging.resolve()
    tree = _Tree(staging)

    # Every copy is planned before any is made, so each way the config can be
    # wrong -- an include entry naming nothing, an asset target outside the
    # root, two sources claiming one path -- is refused with nothing written.
    for name in config.build.include:
        source = _include_source(config, name)
        tree.to_copy(_pairs(source, staging / name), f'include entry {name!r}')

    static_dir = config.root / 'static'
    if static_dir.is_dir():
        for source in (p for p in fs.glob(static_dir, '**/*') if p.is_file()):
            tree.to_copy(
                _pairs(source, staging / source.relative_to(static_dir)),
                'the static/ overlay',
            )

    # context.load already refuses a target outside the root, so this is the
    # same question asked of a Context that came from somewhere else.
    for target, source in sorted(ctx.assets.items()):
        tree.to_copy(
            _pairs(source, config.rebase(target, staging, 'asset target')),
            f'asset {config.display_path(target)}',
        )

    tree.copy_all()

    # The generating steps report what they wrote, so their paths are known
    # only once they have written them -- which is why they run after the
    # copies rather than joining the plan.
    tree.generated(notebooks.generate(config, ctx, staging), 'the generated notebooks')
    tree.generated(deps.write_deps(config, staging), 'the derived pyproject and lock')

    return tree.targets


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
