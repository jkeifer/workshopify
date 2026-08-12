"""The subprocess boundary, wrapped once, for the whole package.

Both programs this tool shells out to -- git and uv -- are run from here, so
every decision that makes running one correctly correct is made once. Nothing
else in the package spells `subprocess`; ruff's banned-api enforces it.
"""

from __future__ import annotations

import subprocess

from collections.abc import Sequence
from pathlib import Path

from workshopify.errors import WorkshopifyError, diagnosed


def run(argv: Sequence[str], cwd: Path) -> str:
    """Run `argv` in `cwd`, returning stdout; a failure names it and stderr.

    There are two ways this fails and they read nothing alike: the program ran
    and objected, where its stderr is the whole diagnostic and anything added
    to it is noise; or the program never ran at all -- nothing on PATH, because
    nothing makes a `pipx install workshopify` have a git or a uv -- which only
    the errno can describe. Both are diagnosed, because catching only the first
    leaves the second a bare traceback out of a CLI whose only rule is that
    failures are diagnostics.

    `check=True` with `capture_output` throws the explanation away and leaves
    the user a bare "returned non-zero exit status 1", so the stderr the
    program went to the trouble of writing is re-raised as the message instead.

    The command is invoked by name, not by absolute path, so the user's own git
    and uv -- their version, config, and credential helpers -- are the ones
    that run. Every argument list is constructed by this package; none is taken
    from user input.
    """
    with diagnosed(f'running {" ".join(argv)} in {cwd}', subprocess.SubprocessError):
        try:
            return subprocess.run(
                argv,
                cwd=cwd,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        except subprocess.CalledProcessError as e:
            raise WorkshopifyError(
                f'error: {" ".join(argv)} failed in {cwd}:\n{e.stderr}',
            ) from e
