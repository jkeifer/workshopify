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

from workshopify.config import Config
from workshopify.errors import WorkshopifyError


@dataclass(frozen=True)
class Context:
    namespace: dict
    assets: dict[Path, Path]


def load(config: Config) -> Context:
    path = (config.root / config.context).resolve()
    if not path.is_file():
        raise WorkshopifyError(f'error: context module {path} does not exist')

    namespace: dict = {'__file__': str(path), 'params': dict(config.params)}
    directory = str(path.parent)
    sys.path.insert(0, directory)
    try:
        exec(compile(path.read_text(), str(path), 'exec'), namespace)  # noqa: S102
    except WorkshopifyError:
        raise
    except Exception as e:
        raise WorkshopifyError(
            f'error: context module {path} failed: {e!r}',
        ) from e
    finally:
        with suppress(ValueError):
            sys.path.remove(directory)

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
    # `root / p` already yields `p` itself when `p` is absolute, so this is
    # the "resolve relative entries against the root" rule for both cases.
    assets = {
        (config.root / target).resolve(): (config.root / source).resolve()
        for target, source in declared.items()
    }
    return Context(namespace=namespace, assets=assets)
