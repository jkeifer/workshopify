"""Rewrite one [tool.workshopify.params] value and re-render.

The param rewrite lands first because rendering reads it back: the context
module is executed against the recorded params, so the render has to see the
value just written rather than the one this call started from.

If anything after the rewrite fails, the rewrite is undone -- and only the
rewrite. Rendered files `render.apply` had already written stay written:
apply reports the paths it changed, not what they held before, so there is
nothing here to restore them from, and buying that would mean reading every
configured file into memory before every `set`. It is not a hole, because
pyproject.toml is the source of truth and apply is idempotent: with the old
param back in place, the next render (or `check`, which will report the
stragglers) re-derives every file from it and converges. What the rollback
buys is the one thing convergence cannot -- that a failed `set` never leaves
pyproject.toml claiming a value nothing could be rendered from.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import tomlkit

from workshopify import context, render
from workshopify.config import Config
from workshopify.errors import WorkshopifyError, diagnosed

# Spellings of a boolean a person types at a shell, where `--flag=1` and
# `--flag=yes` are as ordinary as `true`. TOML itself only writes true/false,
# so this is about what is accepted, never about what is stored.
BOOLEANS = {
    'true': True,
    'yes': True,
    'on': True,
    '1': True,
    'false': False,
    'no': False,
    'off': False,
    '0': False,
}


def _coerced(key: str, value: str, current: Any) -> Any:
    """`value` retyped to match the param it replaces.

    argv is only ever text and TOML is typed, so writing the raw argument back
    would retype the param: `set port 8080` turns `port = 8080` into
    `port = "8080"`, and the next context load hands the generators a string
    where they had a number -- a failure that surfaces somewhere else entirely,
    in whatever the generator did with it. set_param has already established
    that the param exists, so its current value is the only statement of what
    type it is meant to have, and there is nothing else to ask.
    """
    if isinstance(current, str):
        return value
    if isinstance(current, bool):
        # Before int: bool is an int subclass, and int('true') is not a bool.
        coerced = BOOLEANS.get(value.strip().lower())
        if coerced is None:
            raise WorkshopifyError(
                f'error: param {key!r} is a boolean; got {value!r}, expected '
                f'one of: {", ".join(sorted(BOOLEANS))}',
            )
        return coerced
    if isinstance(current, int | float):
        kind = type(current)
        with diagnosed(
            f'param {key!r} is {kind.__name__}, given {value!r}',
            ValueError,
        ):
            return kind(value)
    raise WorkshopifyError(
        f'error: param {key!r} holds a {type(current).__name__}, which `set` '
        f'has no way to spell from a single command-line string; '
        f'edit pyproject.toml directly',
    )


def set_param(config: Config, key: str, value: str) -> list[Path]:
    if key not in config.params:
        available = ', '.join(sorted(config.params)) or '(none defined)'
        raise WorkshopifyError(
            f'error: no param {key!r} in [tool.workshopify.params]; '
            f'available: {available}',
        )

    # Before the file is touched, so a value of the wrong type is refused
    # without a rewrite to undo.
    coerced = _coerced(key, value, config.params[key])

    original = config.pyproject.read_text()
    doc = tomlkit.parse(original)
    doc['tool']['workshopify']['params'][key] = coerced
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
