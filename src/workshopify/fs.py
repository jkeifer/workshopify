"""The filesystem boundary, wrapped once, for the whole package.

Every read, write, copy, and listing this tool does goes through here, so the
two rules that govern them are stated once instead of being restated -- and
forgotten -- at each of the twenty-odd places that touch a file.

The first rule is that a filesystem failure is a diagnostic. `errors.diagnosed`
already says that belongs "once, at each boundary"; this module is what makes
the filesystem one boundary rather than one per call site. Without it a missing
asset or an unwritable output directory reaches the user as a traceback out of
a CLI whose only rule is that failures are diagnostics.

The second is that mode and encoding are the source's, never the machine's:

- `copy` is `shutil.copy`, never `copyfile`. copyfile leaves an existing
  destination's own mode alone and mints a fresh one from the umask otherwise,
  so a `setup.sh` ships executable from one path and not from another, and
  byte-identical content takes a different git mode on a rebuild into a reused
  worktree than in a fresh one.
- Text is UTF-8 on the way in and on the way out. Everything here is source
  text whose format says UTF-8 -- TOML, py:percent, .ipynb, markdown -- and
  `read_text`'s locale default would make what a build produces depend on the
  machine that ran it, which is the whole of what "generated output is a pure
  function of its source" forbids. Choosing the encoding is also what makes a
  byte the file's format forbids *this* module's failure to report, which is
  why `_TEXT` is handed to `diagnosed`: a decode error is a `ValueError`, so
  without it the one module that exists to abolish tracebacks at this boundary
  would raise the one it had just made possible.

Line endings are deliberately not pinned. `write_text` translates `\\n` to
`os.linesep`, which on Windows would be the same determinism defect as the
locale encoding -- but no platform this ships on has an `os.linesep` that is
not `\\n`, and on the read side universal-newline translation is tolerance
worth keeping. Tolerant read, verbatim write, stated rather than assumed.

Nothing here decides what to do about a failure; it raises, and `cli.py` is
still the only place that becomes an exit status.
"""

from __future__ import annotations

import shutil

from pathlib import Path

from workshopify.errors import diagnosed

_ENCODING = 'utf-8'
# Both are ValueErrors, which `diagnosed` does not catch by default; they are
# this module's to own because pinning the encoding is what can raise them.
_TEXT = (UnicodeDecodeError, UnicodeEncodeError)


def read(path: Path) -> str:
    with diagnosed(f'reading {path}', *_TEXT):
        return path.read_text(encoding=_ENCODING)


def read_bytes(path: Path) -> bytes:
    with diagnosed(f'reading {path}'):
        return path.read_bytes()


def write(path: Path, text: str) -> None:
    """Write `text` verbatim, creating the directory it lands in.

    Nothing is appended: `jupytext.writes` omits the trailing newline, so its
    callers add one, while a rendered notes document already terminates itself
    and would gain a blank line. Serving both means appending nothing and
    letting each caller say what it wants.

    The parent is created unconditionally because every path written through
    here is a build output whose directory this tool owns -- there is no
    caller for which "the directory must already exist" is the contract. The
    alternative is `mkdir` restated at each call site.
    """
    with diagnosed(f'writing {path}', *_TEXT):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding=_ENCODING)


def copy(source: Path, dest: Path) -> None:
    """Copy one file, creating the directory it lands in. Mode is the source's."""
    with diagnosed(f'copying {source} to {dest}'):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, dest)


def glob(root: Path, pattern: str) -> list[Path]:
    """Every path under `root` matching `pattern`, sorted.

    Listed here rather than at the call sites because `Path.glob` is lazy: an
    unreadable directory would otherwise raise while the caller is iterating,
    outside the block that would have diagnosed it.

    Sorted for the callers that copy what comes back, where the order the
    filesystem happens to yield is the one input to a build that is not a
    function of the source. `render._files` sorts again, but over the union of
    several patterns -- a merge this cannot do for it, not a restatement.

    `glob(dir, '*')` is a plain directory listing, `iterdir` included: pathlib
    matches dotfiles. That is why nothing in this package calls `iterdir`.
    """
    with diagnosed(f'listing {root}'):
        return sorted(root.glob(pattern))
