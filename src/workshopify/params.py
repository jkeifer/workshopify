"""Rewrite one [tool.workshopify.params] value and re-render, atomically.

The param rewrite lands first because rendering reads it back (the context
module is executed against the recorded params); if anything after the
rewrite fails, the original pyproject text is restored so a failure modifies
nothing.
"""

from __future__ import annotations

from pathlib import Path

import tomlkit

from workshopify import context, render
from workshopify.config import Config
from workshopify.errors import WorkshopifyError


def set_param(config: Config, key: str, value: str) -> list[Path]:
    if key not in config.params:
        available = ', '.join(sorted(config.params)) or '(none defined)'
        raise WorkshopifyError(
            f'error: no param {key!r} in [tool.workshopify.params]; '
            f'available: {available}',
        )

    original = config.pyproject.read_text()
    doc = tomlkit.parse(original)
    doc['tool']['workshopify']['params'][key] = value
    config.pyproject.write_text(tomlkit.dumps(doc))

    try:
        # Deliberately re-read rather than reuse the Config passed in: the
        # context module is executed against the recorded params, so rendering
        # must see the value just written, not the one this call started from.
        fresh = Config.load(config.root)
        ctx = context.load(fresh)
        return render.apply(fresh, ctx)
    except Exception:
        config.pyproject.write_text(original)
        raise
