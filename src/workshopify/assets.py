"""The binary files a workshop carries rather than renders.

An asset is a screenshot, a diagram, a data file -- something the repo ships
byte for byte. The context module declares them in its ASSETS dict and
workshopify.context resolves both halves of each pair against the repo root;
what is left, and what lives here, is putting the file where it was declared
to go and noticing when it is not there.

Three operations, over the loaded Context:

    stale(ctx)              the copies the repo is missing, and the refusal if
                            a declared source does not exist
    apply(stale)            make them; the targets written
    errors(config, ctx)     one string per asset that is missing, uncopied, or
                            divergent

`stale` and `apply` are two halves of one operation on purpose: deciding
cannot be skipped, because deciding is what produces the list `apply` takes.
See `stale`.

This is the only part of `workshopify render` that is not about cog: an
asset's content is a function of nothing but itself, which is the whole
distinction between one and a rendered source. `build` copies assets a second
time, from their sources into the published tree, and does not come through
here -- it has a staging directory to rebase onto and no notion of staleness,
the tree being new every time.
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

    One traversal, because both callers need the whole verdict before they act
    on any of it. Sorted by target so the diagnostics and the copies come out
    in the same order every run.
    """
    return [
        (target, source, problem)
        for target, source in sorted(ctx.assets.items())
        if (problem := _problem(target, source)) is not None
    ]


def stale(ctx: Context) -> list[tuple[Path, Path]]:
    """The copies that would bring the repo up to date, or a refusal.

    The deciding half, and it is separate from `apply` because the caller has
    to be able to take it before it writes anything of its own: a screenshot
    renamed out from under the context module must be caught while the tree is
    still untouched, not between two files it has already rewritten.

    Both answers come out of one traversal -- whether any source is missing,
    and what to copy -- so a caller cannot copy without having asked, and
    cannot ask without getting the list it then copies from. The ordering
    `render.apply` guarantees is therefore a value it holds rather than a rule
    it remembers.
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
    place. That is recoverable rather than atomic and deliberately so -- see
    `render.apply`, whose guarantee this is half of.
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
