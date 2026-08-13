"""A self-contained fixture workshop repo, and the helpers that act on it.

Every workshopify test runs against a repo built here under tmp_path. None of
them may touch the host repository: that is what makes the package portable,
and what lets its tests run without a guard protecting the developer's real
checkout.

The helpers below are plain functions rather than fixtures on purpose. Almost
every test edits the fixture repo before loading it, so anything eager would
describe the repo as it was handed over rather than as the test left it.
"""

from __future__ import annotations

import subprocess

from pathlib import Path

import pytest

from workshopify import context
from workshopify.config import Config
from workshopify.context import Context

PYPROJECT = """\
[project]
name = "fixture-workshop"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = []

[tool.uv]
package = false

[dependency-groups]
dev = []

[tool.workshopify]
context = "context.py"

[tool.workshopify.render]
files = ["src/*.py"]

[tool.workshopify.build]
include = ["Dockerfile"]

[tool.workshopify.params]
greeting = "hello"

[tool.jupytext]
formats = "src///py:percent,notebooks/completed///ipynb"
notebook_metadata_filter = "-all"
cell_metadata_filter = "tags,-all"

[tool.ipynb-scrubber.options]
# Mirrors the real workshop: a cleared cell keeps only its "(See notes: ...)"
# reference. Here so the fixture exercises an empty clear-text at all, which
# is the setting a scrubber upgrade is most likely to reinterpret.
clear-text = ""

[[tool.ipynb-scrubber.files]]
input = "notebooks/completed/01_example.ipynb"
output = "notebooks/01_example.ipynb"
notes-file = "notes/01_example.md"
"""

CONTEXT = """\
word = params['greeting']


def shout() -> str:
    return word.upper()


ASSETS = {'notebooks/assets/logo.png': 'assets/logo.png'}
"""

SRC = """\
# %% [markdown]
# # Example
#
# <!--[[[cog md(shout()) ]]]-->
# HELLO
# <!--[[[end]]]-->

# %%
#| scrub-note: example-note
# a note that lands in the notes file

# %% tags=["scrub-clear"]
# <!--[[[cog cog.outl(f'word = "{word}"') ]]]-->
word = "hello"
# <!--[[[end]]]-->
print(word)
"""


@pytest.fixture(autouse=True)
def _no_ambient_skip_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment `check` reads is the suite's to state, not the shell's.

    `WORKSHOPIFY_SKIP_ASSETS` is answerable from the environment precisely so
    that something other than the argv can set it, and a developer who exported
    it to get a real workshop's hooks past a checkout without assets is then
    running this suite against a different command than it describes. Set to an
    unparseable word it fails three tests for a usage error none of them is
    about; set to `1` it fails nothing and hollows out two, which assert 0 and
    get it because the asset half never ran. The second is the worse one, so
    the guarantee is the suite's rather than each test's to remember.
    """
    monkeypatch.delenv('WORKSHOPIFY_SKIP_ASSETS', raising=False)


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    root = tmp_path / 'repo'
    (root / 'src').mkdir(parents=True)
    (root / 'assets').mkdir()
    (root / 'static').mkdir()
    (root / 'pyproject.toml').write_text(PYPROJECT)
    (root / 'context.py').write_text(CONTEXT)
    (root / 'src' / '01_example.py').write_text(SRC)
    (root / 'assets' / 'logo.png').write_bytes(b'\x89PNG not really')
    (root / 'Dockerfile').write_text('FROM scratch\n')
    (root / 'static' / 'README.md').write_text('participant readme\n')
    return root


def git_repo(path: Path, files: dict[str, str] | None = None) -> Path:
    """Turn a directory into a committed git repo (identity configured).

    The commit is --allow-empty because a publish target starts out with
    nothing in it, and prepare_worktree branches from HEAD: a repo with no
    commit at all has no HEAD to branch from. `files` seeds content that
    initial commit then carries, for the callers whose branch has to hold
    something before the test begins.
    """
    path.mkdir(parents=True, exist_ok=True)
    for name, text in (files or {}).items():
        (path / name).write_text(text)
    subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=path, check=True)
    subprocess.run(['git', 'config', 'user.email', 't@t'], cwd=path, check=True)
    subprocess.run(['git', 'config', 'user.name', 't'], cwd=path, check=True)
    subprocess.run(['git', 'add', '-A'], cwd=path, check=True)
    subprocess.run(
        ['git', 'commit', '-q', '--allow-empty', '-m', 'init'],
        cwd=path,
        check=True,
    )
    return path


def commit_all(path: Path) -> None:
    """Commit everything in a repo, so a later edit is what `git status` sees."""
    subprocess.run(['git', 'add', '-A'], cwd=path, check=True)
    subprocess.run(['git', 'commit', '-qm', 'x'], cwd=path, check=True)


def load_workshop(root: Path) -> tuple[Config, Context]:
    """The config and context every command runs against, loaded from `root`.

    Called rather than injected: a test that edits pyproject.toml or context.py
    is editing exactly what this reads, so loading eagerly would hand it the
    state it was about to change and assert against the wrong workshop.
    """
    config = Config.load(root)
    return config, context.load(config)


def rewrite(root: Path, old: str, new: str) -> None:
    """Edit the fixture repo's pyproject.toml in place.

    The assert is the point: a str.replace that matches nothing rewrites
    nothing and raises nothing, so a fixture that drifts out from under a test
    leaves it passing against a repo it never actually set up.
    """
    pyproject = root / 'pyproject.toml'
    text = pyproject.read_text()
    assert old in text
    pyproject.write_text(text.replace(old, new))


def uv_lock(repo: Path, *args: str) -> None:
    """The repo's own lock, which a build derives the participants' lock from.

    The suite's one hard dependency on a real `uv` on PATH, in one place so
    that dependency is visible. Extra arguments pass through for the callers
    that must resolve against a local index and never the network.
    """
    subprocess.run(
        ['uv', 'lock', *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )
