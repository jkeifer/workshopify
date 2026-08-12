"""Execute the repo's context module: the procedural half of the config.

The context module is the one piece of a workshop repo that must be code --
its module-level names become the namespace the cog generators run in, and
its optional ASSETS dict declares binary files derived from that namespace
(copied on apply, byte-verified on check).

It executes with `params` (the [tool.workshopify.params] table) and
`__file__` pre-bound, and with its own directory on sys.path so sibling
modules import normally. Executed fresh on every load, so a caller that just
rewrote a param gets a namespace reflecting the rewrite.
"""

from __future__ import annotations

import sys

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workshopify import fs
from workshopify.config import Config
from workshopify.errors import WorkshopifyError


@dataclass(frozen=True)
class Context:
    namespace: dict[str, Any]
    assets: dict[Path, Path]


def _executed(config: Config, path: Path) -> dict[str, Any]:
    """The module at `path` run, and the names it left behind.

    `params` and `__file__` are pre-bound, and the module's own directory
    goes on sys.path for the duration so a sibling import resolves the way it
    would if the module were run directly. Removed in a `finally` because the
    entry outlives a failed exec otherwise, and the next repo loaded in the
    same process would import this one's siblings.

    A `WorkshopifyError` from the module is the author diagnosing something
    themselves -- a missing screenshot, a param that names nowhere -- so it
    passes through as written. Anything else is a crash in code this package
    is executing on the author's behalf, which is worth its repr and the name
    of the file to look in.
    """
    namespace: dict[str, Any] = {'__file__': str(path), 'params': dict(config.params)}
    directory = str(path.parent)
    sys.path.insert(0, directory)
    try:
        exec(compile(fs.read(path), str(path), 'exec'), namespace)  # noqa: S102
    except WorkshopifyError:
        raise
    except Exception as e:
        raise WorkshopifyError(
            f'error: context module {path} failed: {e!r}',
        ) from e
    finally:
        with suppress(ValueError):
            sys.path.remove(directory)
    return namespace


def _assets(config: Config, path: Path, namespace: dict[str, Any]) -> dict[Path, Path]:
    """The declared ASSETS, checked and resolved to absolute target -> source.

    Everything here is the workshop author's typing, so every way of getting
    it wrong is diagnosed against the spelling they used and the file they
    used it in -- which is the whole reason this is validated at declaration
    rather than where a copy later fails.
    """
    declared = namespace.get('ASSETS', {})
    if not isinstance(declared, dict):
        raise WorkshopifyError(
            f'error: {path}: ASSETS must map target path -> source path',
        )
    bad = [
        p for pair in declared.items() for p in pair if not isinstance(p, str | Path)
    ]
    if bad:
        raise WorkshopifyError(
            f'error: {path}: ASSETS paths must be str or Path, not {bad!r}',
        )
    assets: dict[Path, Path] = {}
    spellings: dict[Path, str] = {}
    for target, source in declared.items():
        # `root / p` already yields `p` itself when `p` is absolute, so this is
        # the "resolve relative entries against the root" rule for both cases.
        resolved = (config.root / target).resolve()
        # A target is where the file lands in the published tree, and there is
        # no such place outside the repo: build would refuse it, so a repo
        # declaring one renders clean forever and can never be published.
        # `within` both refuses that and hands back the repo's own spelling of
        # the target, which is what the duplicate below has to be reported by:
        # the two declarations differ precisely in how they spell it.
        relative = config.within(resolved, f'{path}: ASSETS target')
        if resolved in assets:
            raise WorkshopifyError(
                f'error: {path}: ASSETS declares {relative} twice, as '
                f'{spellings[resolved]!r} and {str(target)!r} -- one target has one '
                f'source, and a dict keeps only the last of them',
            )
        spellings[resolved] = str(target)
        assets[resolved] = (config.root / source).resolve()
    return assets


def load(config: Config) -> Context:
    """The context module, executed, with what it declared alongside it."""
    path = (config.root / config.context).resolve()
    if not path.is_file():
        raise WorkshopifyError(f'error: context module {path} does not exist')
    namespace = _executed(config, path)
    return Context(namespace=namespace, assets=_assets(config, path, namespace))
