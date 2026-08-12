"""The binary files a workshop carries rather than renders.

An asset is a screenshot, a diagram, a data file: something the repo ships
byte for byte. workshopify.context resolves both halves of each declared pair
against the repo root, so what is left is putting each file where it was
declared to go and noticing when it is not there.

Three operations, over the loaded Context:

    stale(ctx)              the copies the repo is missing, and the refusal if
                            a declared source does not exist
    apply(stale)            make them; the targets written
    errors(config, ctx)     one string per asset that is missing, uncopied, or
                            divergent

`stale` decides and `apply` acts; see `stale` for why they are separate.

Nothing here is about cog: an asset's content is a function of nothing but
itself, which is the whole distinction between one and a rendered source.
`build` copies assets a second time into the published tree without coming
through here, having a staging directory to rebase onto and no notion of
staleness, the tree being new every time.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum, auto
from pathlib import Path

from workshopify import fs
from workshopify.config import Config
from workshopify.context import Context
from workshopify.errors import WorkshopifyError


class Problem(Enum):
    """What is wrong with one declared asset. See `_problem`."""

    NO_SOURCE = auto()
    NOT_COPIED = auto()
    DIFFERS = auto()


def _problem(target: Path, source: Path) -> Problem | None:
    """The single statement of what makes an asset wrong; None if it is right.

    What a problem costs is the caller's call, and the two callers price them
    differently: to `errors` all three are errors, while to `stale` only a
    missing source is -- the other two are the work this module exists to do.
    """
    if not source.is_file():
        return Problem.NO_SOURCE
    if not target.is_file():
        return Problem.NOT_COPIED
    if fs.read_bytes(target) != fs.read_bytes(source):
        return Problem.DIFFERS
    return None


def _problems(ctx: Context) -> list[tuple[Path, Path, Problem]]:
    """Every declared asset that is wrong, with what is wrong with it.

    Sorted by target so the diagnostics and the copies come out in the same
    order every run.
    """
    return [
        (target, source, problem)
        for target, source in sorted(ctx.assets.items())
        if (problem := _problem(target, source)) is not None
    ]


def stale(ctx: Context) -> list[tuple[Path, Path]]:
    """The copies that would bring the repo up to date, or a refusal.

    Separate from `apply` because the caller must be able to decide before it
    writes anything of its own: a screenshot renamed out from under the
    context module has to be caught while the tree is still untouched, not
    between two files already rewritten.

    One traversal answers both -- whether a source is missing, and what to
    copy -- so a caller cannot copy without having asked.
    """
    problems = _problems(ctx)
    missing = sorted(
        source for _, source, problem in problems if problem is Problem.NO_SOURCE
    )
    if missing:
        raise WorkshopifyError(
            f'error: asset source(s) do not exist: '
            f'{", ".join(str(s) for s in missing)}',
        )
    # Every remaining problem is one a copy fixes: NO_SOURCE was the only kind
    # that is an error rather than work, and it has already raised.
    return [(target, source) for target, source, _ in problems]


def apply(stale: Iterable[tuple[Path, Path]]) -> list[Path]:
    """Make the copies `stale` decided on; the targets written.

    Sequential, so a permissions error partway leaves the earlier copies in
    place: recoverable rather than atomic, which `render.apply` explains.
    """
    written = []
    for target, source in stale:
        fs.copy(source, target)
        written.append(target)
    return written


def errors(config: Config, ctx: Context) -> list[str]:
    """One diagnostic per wrong asset, for a caller that reports rather than fixes."""
    messages = []
    for target, source, problem in _problems(ctx):
        name = config.display_path(target)
        match problem:
            case Problem.NO_SOURCE:
                messages.append(f'{source}: missing; cannot check {name}')
            case Problem.NOT_COPIED:
                messages.append(f'{name}: missing (expected a copy of {source})')
            case Problem.DIFFERS:
                messages.append(f'{name}: does not match {source}')
    return messages
