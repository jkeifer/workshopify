"""Derive the participants' pyproject.toml and uv.lock from the repo's own.

A workshop repo's pyproject carries the authoring machinery -- the workspace
wiring, the dev group, the lint config -- none of which participants can use or
should see. This module turns that pyproject and its lock into the pair the
published tree ships, keeping the comments that make the result readable and
the versions contributors actually develop against.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import tomlkit

from tomlkit.exceptions import ParseError

from workshopify import fs, proc
from workshopify.config import Config
from workshopify.errors import WorkshopifyError, diagnosed

type _Table = MutableMapping[str, Any]
type _Paths = dict[str, _Paths]


def _overlap(kind: str, ancestor: str, descendant: str) -> WorkshopifyError:
    """The diagnostic for a path listed together with one of its descendants."""
    return WorkshopifyError(
        f'error: {kind} path {ancestor!r} already names everything under it, '
        f'so listing {descendant!r} too says two different things; keep one',
    )


def _paths(dotted: tuple[str, ...], kind: str) -> _Paths:
    """Dotted paths as a tree of keys. A leaf names a whole subtree.

    Which is why a path listed alongside one of its own descendants is an
    error rather than a reading to pick: `('tool.uv', 'tool.uv.sources')` asks
    both for all of [tool.uv] and for only its [sources], and the tree has no
    room to hold both. The narrow reading is the one that used to win, quietly
    -- it shipped [tool.uv.sources] and dropped `package = false` -- and a
    silently wrong pyproject is exactly what `_resolve` refuses for a typo.
    """
    root: _Paths = {}
    leaves: set[str] = set()
    for path in dotted:
        parts = path.split('.')
        node = root
        for depth, part in enumerate(parts[:-1], 1):
            ancestor = '.'.join(parts[:depth])
            if ancestor in leaves:
                raise _overlap(kind, ancestor, path)
            node = node.setdefault(part, {})
        node = node.setdefault(parts[-1], {})
        # Non-empty means a descendant of this path was listed first; the same
        # contradiction, met from the other side.
        if node:
            raise _overlap(kind, path, f'{path}.{next(iter(node))}')
        leaves.add(path)
    return root


def _resolve(table: _Table, key: str, kind: str, path: str) -> _Table:
    """The node at `key`, or the diagnostic for a path that matches nothing."""
    if not isinstance(table, MutableMapping) or key not in table:
        raise WorkshopifyError(
            f'error: {kind} path {path!r} matches nothing in pyproject.toml',
        )
    return table[key]


def _keep_only(table: _Table, paths: _Paths, prefix: str = '') -> None:
    """Delete everything `paths` does not name. A leaf keeps its subtree."""
    for key, children in paths.items():
        node = _resolve(table, key, 'keep', prefix + key)
        if children:
            _keep_only(node, children, f'{prefix}{key}.')
    for key in list(table):
        if key not in paths:
            del table[key]


def _drop_all(table: _Table, paths: _Paths, prefix: str = '') -> None:
    """Delete exactly what `paths` names, each with its subtree."""
    for key, children in paths.items():
        node = _resolve(table, key, 'drop', prefix + key)
        if children:
            _drop_all(node, children, f'{prefix}{key}.')
        else:
            del table[key]


def derive_pyproject(text: str, keep: tuple[str, ...], drop: tuple[str, ...]) -> str:
    """The repo's pyproject with the authoring machinery stripped out.

    tomlkit rather than a tomllib/tomli-w round trip so the comments that make
    the published pyproject readable survive.

    Both `keep` and `drop` are dotted paths of any depth, read the same way: a
    path names a node, and naming a node takes its whole subtree with it. So
    `keep = ('project', 'tool.uv')` ships all of [tool.uv] and nothing else
    under [tool], and `drop` then carves pieces back out of what was kept --
    the workspace wiring under [tool.uv] must not reach participants, who have
    no workshopify checkout to point at.

    A path on either side that matches nothing is an error, not a no-op: a
    typo in `keep` would silently ship a pyproject missing a whole table, and
    one in `drop` would silently ship the wiring `drop` exists to remove. A
    path listed with one of its own descendants is an error for the same
    reason -- see `_paths`.
    """
    # Named for the text it was given, not a path: this takes a document to
    # transform, and the file it came from is the caller's to know.
    with diagnosed('parsing the source pyproject.toml', ParseError):
        doc = tomlkit.parse(text)
    _keep_only(doc, _paths(keep, 'keep'))
    _drop_all(doc, _paths(drop, 'drop'))
    return tomlkit.dumps(doc)


def write_deps(config: Config, staging: Path) -> list[Path]:
    """Write the workshop's pyproject.toml and uv.lock into staging.

    The lock is seeded from the repo's own lock and re-resolved offline, so
    it is a derivation of that lock rather than an independent resolution:
    participants get exactly the versions contributors develop against, and
    the build needs no network. So a repo that never ran `uv lock` has nothing
    to derive from, and is told so in those words: left to the copy it is an
    ENOENT on a path the author never chose to write, naming neither the state
    nor the one command that fixes it.

    Returns what it wrote, because the build reports its output from what each
    writing step hands back rather than from a glob. A caller restating these
    two names is a second place to edit when one moves, and the failure that
    follows is silent: the cruft check accuses a file the build still writes.
    """
    seed = config.root / 'uv.lock'
    if not seed.is_file():
        raise WorkshopifyError(
            f'error: no lock to derive from at {seed}; run `uv lock` first',
        )

    pyproject = staging / 'pyproject.toml'
    lock = staging / 'uv.lock'
    fs.write(
        pyproject,
        derive_pyproject(
            fs.read(config.pyproject),
            config.build.keep_tables,
            config.build.drop_keys,
        ),
    )
    fs.copy(seed, lock)
    proc.run(['uv', 'lock', '--offline'], staging)

    return [pyproject, lock]
