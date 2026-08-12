"""The one exception type the whole package reports failures with."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager


class WorkshopifyError(Exception):
    """A diagnosed failure, carrying the message the user should see.

    The message is the whole diagnostic, `error:` prefix included, so the CLI
    boundary can print it unchanged. Deciding to exit is not the same act as
    detecting a problem: everything below `cli.main()` raises this; only the
    CLI turns one into an exit status.
    """


@contextmanager
def diagnosed(what: str, *also: type[BaseException]) -> Iterator[None]:
    """Foreign-boundary failures, restated as the diagnostic the CLI prints.

    The invariant that every module below `cli.py` raises `WorkshopifyError`
    only holds where the package meets code that does not know about it: the
    filesystem, subprocesses, cog. Those are the calls that turn a missing
    lockfile or a typo'd generator into a traceback, so this is where the
    invariant is enforced -- once, at each boundary -- rather than restated as
    a bespoke `try` in every module that has one.

    `OSError` is caught everywhere because every boundary can raise it.
    Anything else a particular boundary owns is passed in by that caller,
    which keeps its library out of this module: `render` hands in cog's error,
    `fs` its decode errors, `proc` its `SubprocessError`, and nothing else has
    to know any of them exist.
    """
    try:
        yield
    except (OSError, *also) as e:
        raise WorkshopifyError(f'error: {what}: {e}') from e
