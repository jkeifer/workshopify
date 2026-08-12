"""Derive the participants' pyproject.toml and uv.lock from the repo's own.

A workshop repo's pyproject carries the authoring machinery -- the workspace
wiring, the dev group, the lint config -- none of which participants can use or
should see. This module turns that pyproject and its lock into the pair the
published tree ships, keeping the comments that make the result readable and
the versions contributors actually develop against.
"""

from __future__ import annotations

import shutil
import subprocess

from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import tomlkit

from workshopify.config import Config
from workshopify.errors import WorkshopifyError

type _Table = MutableMapping[str, Any]
type _Paths = dict[str, _Paths]


def _paths(dotted: tuple[str, ...]) -> _Paths:
    """Dotted paths as a tree of keys. A leaf names a whole subtree."""
    root: _Paths = {}
    for path in dotted:
        node = root
        for part in path.split('.'):
            node = node.setdefault(part, {})
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
    one in `drop` would silently ship the wiring `drop` exists to remove.
    """
    doc = tomlkit.parse(text)
    _keep_only(doc, _paths(keep))
    _drop_all(doc, _paths(drop))
    return tomlkit.dumps(doc)


def write_deps(config: Config, staging: Path) -> None:
    """Write the workshop's pyproject.toml and uv.lock into staging.

    The lock is seeded from the repo's own lock and re-resolved offline, so
    it is a derivation of that lock rather than an independent resolution:
    participants get exactly the versions contributors develop against, and
    the build needs no network.
    """
    (staging / 'pyproject.toml').write_text(
        derive_pyproject(
            config.pyproject.read_text(),
            config.build.keep_tables,
            config.build.drop_keys,
        ),
    )
    shutil.copyfile(config.root / 'uv.lock', staging / 'uv.lock')
    try:
        subprocess.run(
            ['uv', 'lock', '--offline'],
            cwd=staging,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise WorkshopifyError(
            f'error: uv lock --offline failed in {staging}:\n{e.stderr}',
        ) from e
