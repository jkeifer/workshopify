import pytest

from conftest import git_repo

from workshopify.errors import WorkshopifyError
from workshopify.git import git, succeeds


def test_git_returns_stdout(tmp_path):
    repo = git_repo(tmp_path / 'r')
    assert git(repo, 'rev-parse', '--abbrev-ref', 'HEAD').strip() == 'main'


def test_git_failure_carries_the_diagnostic(tmp_path):
    repo = git_repo(tmp_path / 'r')
    with pytest.raises(WorkshopifyError) as excinfo:
        git(repo, 'rev-parse', '--verify', 'no-such-ref')
    assert 'rev-parse' in str(excinfo.value)


def test_a_git_that_cannot_run_is_still_a_diagnostic(tmp_path, monkeypatch):
    """No git on PATH is how workshopify runs outside `uv run`, not a traceback."""
    repo = git_repo(tmp_path / 'r')
    monkeypatch.setenv('PATH', str(tmp_path / 'empty'))
    with pytest.raises(WorkshopifyError, match='rev-parse'):
        git(repo, 'rev-parse', 'HEAD')


def test_succeeds_probes_quietly(tmp_path):
    repo = git_repo(tmp_path / 'r')
    assert succeeds(repo, 'rev-parse', '--git-dir')
    assert not succeeds(repo, 'show-ref', '--verify', '--quiet', 'refs/heads/nope')


def test_a_probe_with_no_git_at_all_answers_no(tmp_path, monkeypatch):
    """The documented cost of routing the probe through `git()`.

    Every caller has already run a real git command by the time it probes, so
    the broken environment is diagnosed before this answer is acted on.
    """
    repo = git_repo(tmp_path / 'r')
    monkeypatch.setenv('PATH', str(tmp_path / 'empty'))
    assert not succeeds(repo, 'rev-parse', '--git-dir')
