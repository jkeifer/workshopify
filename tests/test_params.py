import os
import shutil

from pathlib import Path

import pytest

from conftest import rewrite

from workshopify import context, fs, render
from workshopify.config import Config
from workshopify.errors import WorkshopifyError
from workshopify.params import set_param

# Two assets, so a copy can be made to fail after another has already landed
# -- the only way to reach a failure that happens *after* apply has written.
TWO_ASSETS = """\
word = params['greeting']


def shout() -> str:
    return word.upper()


ASSETS = {
    'notebooks/assets/logo.png': 'assets/logo.png',
    'notebooks/assets/second.png': 'assets/second.png',
}
"""


def _typed(fixture_repo, line):
    """The fixture's params table with one more param of a non-string type."""
    rewrite(fixture_repo, 'greeting = "hello"', f'greeting = "hello"\n{line}')
    return Config.load(fixture_repo)


def test_set_rewrites_the_param_and_rerenders(fixture_repo):
    rewritten = set_param(Config.load(fixture_repo), 'greeting', 'kia ora')
    text = (fixture_repo / 'pyproject.toml').read_text()
    assert 'greeting = "kia ora"' in text
    src = (fixture_repo / 'src/01_example.py').read_text()
    assert 'word = "kia ora"' in src
    assert '# KIA ORA' in src
    assert any(p.name == '01_example.py' for p in rewritten.paths)


def test_set_preserves_pyproject_comments(fixture_repo):
    rewrite(
        fixture_repo,
        '[tool.workshopify.params]',
        '# which greeting ships\n[tool.workshopify.params]',
    )
    set_param(Config.load(fixture_repo), 'greeting', 'howdy')
    assert '# which greeting ships' in (fixture_repo / 'pyproject.toml').read_text()


def test_set_rejects_an_unknown_param(fixture_repo):
    with pytest.raises(WorkshopifyError, match="no param 'nope'"):
        set_param(Config.load(fixture_repo), 'nope', 'x')
    assert 'nope' not in (fixture_repo / 'pyproject.toml').read_text()


def test_set_rolls_back_when_rendering_fails(fixture_repo):
    """A failure must modify nothing: the param rewrite lands first because
    rendering reads it back, so a failing render undoes the rewrite."""
    original = (fixture_repo / 'pyproject.toml').read_text()
    src_before = (fixture_repo / 'src/01_example.py').read_text()
    (fixture_repo / 'context.py').write_text(
        "if params['greeting'] == 'boom':\n"
        "    raise RuntimeError('kaboom')\n"
        "word = params['greeting']\n"
        'def shout():\n'
        '    return word.upper()\n',
    )
    with pytest.raises(WorkshopifyError):
        set_param(Config.load(fixture_repo), 'greeting', 'boom')
    assert (fixture_repo / 'pyproject.toml').read_text() == original
    assert (fixture_repo / 'src/01_example.py').read_text() == src_before


def test_set_rolls_back_only_the_param_when_a_later_write_fails(
    fixture_repo,
    monkeypatch,
):
    """The rollback the docstring claims, at the point the earlier test cannot
    reach: a failure *after* apply has already written some of the tree."""
    (fixture_repo / 'assets/second.png').write_bytes(b'\x89PNG second')
    (fixture_repo / 'context.py').write_text(TWO_ASSETS)
    original = (fixture_repo / 'pyproject.toml').read_text()
    src = fixture_repo / 'src/01_example.py'
    copy = shutil.copy

    def fail_on_second(source, target, **kwargs):
        if Path(target).name == 'second.png':
            raise PermissionError(target)
        return copy(source, target, **kwargs)

    monkeypatch.setattr(fs.shutil, 'copy', fail_on_second)
    with pytest.raises(WorkshopifyError, match=r'second\.png'):
        set_param(Config.load(fixture_repo), 'greeting', 'kia ora')

    # The param is back, and the files apply had already written are not:
    # set_param undoes its own write, and nothing else.
    assert (fixture_repo / 'pyproject.toml').read_text() == original
    assert 'word = "kia ora"' in src.read_text()
    assert (fixture_repo / 'notebooks/assets/logo.png').is_file()

    # Which is why the docstring calls it convergence rather than atomicity:
    # pyproject is the source of truth, and a re-render restores the tree to
    # agree with it.
    monkeypatch.undo()
    config = Config.load(fixture_repo)
    render.apply(config, context.load(config))
    assert 'word = "hello"' in src.read_text()
    assert render.check(config, context.load(config)) == []


def test_set_coerces_to_the_type_the_param_already_has(fixture_repo):
    config = _typed(fixture_repo, 'port = 8080\ndebug = false')
    port = set_param(config, 'port', '9090')
    debug = set_param(Config.load(fixture_repo), 'debug', 'yes')
    params = Config.load(fixture_repo).params
    assert params['port'] == 9090
    assert params['debug'] is True
    assert 'port = 9090' in (fixture_repo / 'pyproject.toml').read_text()
    # What the CLI reports is the recorded value, not the argv it was given:
    # reporting the latter would spell an int param back as a string.
    assert (port.value, debug.value) == (9090, True)


def test_set_refuses_a_value_of_the_wrong_type(fixture_repo):
    config = _typed(fixture_repo, 'port = 8080')
    with pytest.raises(WorkshopifyError, match=r"param 'port' is int, given 'http'"):
        set_param(config, 'port', 'http')
    # Refused before the rewrite, so there is nothing to roll back.
    assert 'port = 8080' in (fixture_repo / 'pyproject.toml').read_text()


def test_set_to_the_current_value_still_restores_assets(fixture_repo):
    target = fixture_repo / 'notebooks/assets/logo.png'
    assert not target.exists()
    set_param(Config.load(fixture_repo), 'greeting', 'hello')
    assert target.is_file()


@pytest.mark.skipif(os.geteuid() == 0, reason='root writes a read-only file anyway')
def test_a_pyproject_that_cannot_be_written_is_diagnosed(fixture_repo):
    """The rewrite is a filesystem boundary like any other.

    It raises before the file is touched, so there is nothing to roll back --
    which is also why the rollback write, the one that runs while another
    failure is already unwinding, must go through the same boundary.
    """
    pyproject = fixture_repo / 'pyproject.toml'
    original = pyproject.read_text()
    pyproject.chmod(0o444)
    try:
        with pytest.raises(WorkshopifyError, match=r'writing .*pyproject\.toml'):
            set_param(Config.load(fixture_repo), 'greeting', 'kia ora')
    finally:
        pyproject.chmod(0o644)
    assert pyproject.read_text() == original


def test_a_failed_rollback_does_not_replace_the_failure_it_was_undoing(
    fixture_repo,
    monkeypatch,
):
    """The rollback runs while another failure is already unwinding.

    If it fails too, reporting only its own error would leave the user reading
    about an unwritable file with no idea what the `set` had been doing -- and
    without the one fact that now matters, that pyproject.toml was left holding
    the new value. Both failures, or the original alone; never the rollback
    alone.
    """
    (fixture_repo / 'context.py').write_text(
        "if params['greeting'] == 'boom':\n"
        "    raise RuntimeError('kaboom')\n"
        "word = params['greeting']\n",
    )
    real_write = fs.write

    def fail_the_rollback(path, text):
        if 'greeting = "hello"' in text:  # the restore, not the rewrite
            raise WorkshopifyError('error: writing pyproject.toml: disk full')
        return real_write(path, text)

    monkeypatch.setattr(fs, 'write', fail_the_rollback)
    with pytest.raises(WorkshopifyError) as excinfo:
        set_param(Config.load(fixture_repo), 'greeting', 'boom')

    message = str(excinfo.value)
    assert 'kaboom' in message, 'the original failure was lost'
    assert 'disk full' in message, 'the rollback failure was lost'
    assert "'greeting' could not be put back" in message
