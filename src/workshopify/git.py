"""One git helper for the whole package, over the shared subprocess boundary.

What makes running a subprocess correctly correct lives in `proc.run`; what
lives here is only that git is spelled `git` and takes its cwd first.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

from workshopify import proc
from workshopify.errors import WorkshopifyError


def git(cwd: Path, *args: str) -> str:
    """Run git in cwd, returning stdout; a failure names the command and stderr."""
    return proc.run(['git', *args], cwd)


def succeeds(cwd: Path, *args: str) -> bool:
    """Whether the command exits 0. For probes where failure is an answer.

    Suppressing every `WorkshopifyError` also answers False when git is
    missing entirely -- an answer, where the honest reply is that the question
    cannot be asked. It hides nothing: a probe is never the first git in a
    flow, `prepare_worktree` having already run `git worktree prune` through
    `git()`, so a broken environment is diagnosed before anything acts on an
    answer this could get wrong.
    """
    with suppress(WorkshopifyError):
        git(cwd, *args)
        return True
    return False
