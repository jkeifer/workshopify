import subprocess

from pathlib import Path

import pytest

from conftest import git_repo

from workshopify.errors import WorkshopifyError
from workshopify.worktree import clean, is_dirty, prepare_worktree


def _repo_with_commit(tmp_path: Path, name: str = 'origin_repo') -> Path:
    repo = git_repo(tmp_path / name)
    (repo / 'a.txt').write_text('a')
    subprocess.run(['git', 'add', '-A'], cwd=repo, check=True)
    subprocess.run(['git', 'commit', '-qm', 'init'], cwd=repo, check=True)
    return repo


def _commit_all(wt):
    subprocess.run(['git', 'add', '-A'], cwd=wt, check=True)
    subprocess.run(['git', 'commit', '-qm', 'x'], cwd=wt, check=True)


# --- prepare_worktree ------------------------------------------------------


def test_creates_a_new_branch_from_head(tmp_path):
    repo = _repo_with_commit(tmp_path)
    wt = prepare_worktree(repo, 'pub', repo / 'pub', orphan=False)
    assert wt.path == (repo / 'pub').resolve()
    assert (wt.path / 'a.txt').is_file()
    # Advisory, not diagnostic: this is where a mistyped branch goes wrong.
    assert any('creating it from' in note for note in wt.notes)


def test_a_prepared_worktree_that_needed_no_explaining_says_nothing(tmp_path):
    repo = _repo_with_commit(tmp_path)
    subprocess.run(['git', 'branch', 'pub'], cwd=repo, check=True)
    assert prepare_worktree(repo, 'pub', repo / 'pub', orphan=False).notes == ()


def test_reuses_an_existing_worktree_at_the_same_path(tmp_path):
    repo = _repo_with_commit(tmp_path)
    first = prepare_worktree(repo, 'pub', repo / 'pub', orphan=False)
    again = prepare_worktree(repo, 'pub', repo / 'pub', orphan=False)
    assert again.path == first.path
    assert again.notes == (), 'reusing a worktree needs no explanation'


def test_refuses_the_branch_checked_out_elsewhere(tmp_path):
    repo = _repo_with_commit(tmp_path)
    prepare_worktree(repo, 'pub', repo / 'pub', orphan=False)
    with pytest.raises(WorkshopifyError, match='already checked out'):
        prepare_worktree(repo, 'pub', repo / 'elsewhere', orphan=False)


def test_refuses_a_path_holding_another_branch(tmp_path):
    repo = _repo_with_commit(tmp_path)
    prepare_worktree(repo, 'one', repo / 'wt', orphan=False)
    with pytest.raises(WorkshopifyError, match='already a worktree'):
        prepare_worktree(repo, 'two', repo / 'wt', orphan=False)


def test_refuses_a_nonempty_directory(tmp_path):
    repo = _repo_with_commit(tmp_path)
    (repo / 'occupied').mkdir()
    (repo / 'occupied' / 'x').write_text('x')
    with pytest.raises(WorkshopifyError, match='not empty'):
        prepare_worktree(repo, 'pub', repo / 'occupied', orphan=False)


def test_tracks_a_remote_branch_on_a_fresh_clone(tmp_path):
    origin = _repo_with_commit(tmp_path)
    subprocess.run(
        ['git', 'branch', 'pub'],
        cwd=origin,
        check=True,
    )
    clone = tmp_path / 'clone'
    subprocess.run(
        ['git', 'clone', '-q', str(origin), str(clone)],
        check=True,
    )
    wt = prepare_worktree(clone, 'pub', clone / 'pub', orphan=False)
    head = subprocess.run(
        ['git', 'rev-parse', '--abbrev-ref', 'pub@{upstream}'],
        cwd=wt.path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head == 'origin/pub'
    assert any('tracking origin/pub' in note for note in wt.notes)


def test_orphan_branch_has_no_history(tmp_path):
    repo = _repo_with_commit(tmp_path)
    wt = prepare_worktree(repo, 'pages', repo / 'pages', orphan=True)
    result = subprocess.run(
        ['git', 'rev-parse', 'HEAD'],
        cwd=wt.path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, 'an orphan worktree starts with no commits'


def test_prunes_a_stale_registration(tmp_path):
    import shutil

    repo = _repo_with_commit(tmp_path)
    wt = prepare_worktree(repo, 'pub', repo / 'pub', orphan=False)
    shutil.rmtree(wt.path)
    # The stale registration would otherwise block re-adding the branch.
    assert prepare_worktree(repo, 'pub', repo / 'pub', orphan=False).path == wt.path


# --- is_dirty / clean ------------------------------------------------------


def test_is_dirty_detects_modified_and_untracked(tmp_path):
    wt = git_repo(tmp_path / 'wt')
    (wt / 'a.txt').write_text('a')
    _commit_all(wt)
    assert not is_dirty(wt)
    (wt / 'a.txt').write_text('changed')
    assert is_dirty(wt)
    (wt / 'a.txt').write_text('a')
    assert not is_dirty(wt)
    (wt / 'new.txt').write_text('n')
    assert is_dirty(wt)


def test_clean_removes_tracked_files_only(tmp_path):
    wt = git_repo(tmp_path / 'wt')
    (wt / 'tracked.txt').write_text('a')
    _commit_all(wt)
    (wt / 'ignored.txt').write_text('i')
    (wt / '.gitignore').write_text('ignored.txt\n')
    clean(wt)
    assert not (wt / 'tracked.txt').exists()
    assert (wt / 'ignored.txt').exists(), 'ignored files must survive'
