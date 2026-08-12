"""One git helper for the whole package, surfacing git's own diagnostics.

`check=True` with `capture_output` throws git's explanation away and leaves
the user a bare "returned non-zero exit status 1"; the stderr git went to the
trouble of writing is re-raised as the error message instead. This helper was
previously duplicated across two scripts with divergent behavior -- one path
gave diagnostics, the other a raw traceback.
"""

from __future__ import annotations

import subprocess

from contextlib import suppress
from pathlib import Path

from workshopify.errors import WorkshopifyError, diagnosed


def git(cwd: Path, *args: str) -> str:
    """Run git in cwd, returning stdout; a failure names the command and stderr.

    There are two ways this fails and they read nothing alike: git ran and
    objected, where its stderr is the whole diagnostic and anything we add is
    noise; or git never ran at all -- no executable on PATH, because nothing
    makes a `pipx install workshopify` have one -- which only the errno can
    describe. Catching just the first left the second a bare traceback out of
    a CLI whose only rule is that failures are diagnostics.
    """
    with diagnosed(f'running git {" ".join(args)} in {cwd}'):
        try:
            return subprocess.run(
                ['git', '-C', str(cwd), *args],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        except subprocess.CalledProcessError as e:
            raise WorkshopifyError(
                f'error: git {" ".join(args)} failed in {cwd}:\n{e.stderr}',
            ) from e


def succeeds(cwd: Path, *args: str) -> bool:
    """Whether the command exits 0. For probes where failure is an answer.

    Through `git()` rather than a second spelling of the argv: this module
    exists because that spelling was once duplicated and drifted, and the copy
    here had already lost `text=True`. One exception built and discarded per
    probe buys back the single spelling, and nothing probes in a loop.

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
