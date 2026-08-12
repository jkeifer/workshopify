import shutil
import subprocess

from pathlib import Path

import pytest

from conftest import commit_all, git_repo

from workshopify import worktree
from workshopify.errors import WorkshopifyError
from workshopify.worktree import clean, is_dirty, prepare_worktree

# --- _add_args -------------------------------------------------------------

ROOT = Path('/repo')
PATH = Path('/repo/pub')


def _decided(monkeypatch, *, local, remotes, orphan, head='main'):
    """`_add_args`'s verdict, with the questions it asks git answered here.

    It decides without mutating, which is what lets all four shapes be checked
    against no repo at all -- the argv is the whole of what distinguishes
    them, and end-to-end tests can only observe what git then did with it.
    """
    monkeypatch.setattr(worktree, '_branch_exists', lambda root, branch: local)
    monkeypatch.setattr(worktree, '_remotes_with_branch', lambda root, branch: remotes)
    monkeypatch.setattr(worktree, 'git', lambda cwd, *args: f'{head}\n')
    return worktree._add_args(ROOT, 'pub', PATH, orphan=orphan)


@pytest.mark.parametrize(
    ('local', 'remotes', 'orphan', 'expected'),
    [
        pytest.param(
            True,
            [],
            False,
            (['/repo/pub', 'pub'], ()),
            id='local-branch',
        ),
        pytest.param(
            False,
            ['origin'],
            False,
            (
                ['--track', '-b', 'pub', '/repo/pub', 'origin/pub'],
                ("creating 'pub' tracking origin/pub",),
            ),
            id='one-remote',
        ),
        pytest.param(
            False,
            [],
            True,
            (['--orphan', '-b', 'pub', '/repo/pub'], ()),
            id='orphan',
        ),
        pytest.param(
            False,
            [],
            False,
            (
                ['-b', 'pub', '/repo/pub'],
                ("note: no local or remote branch 'pub'; creating it from main",),
            ),
            id='new-from-head',
        ),
        # An existing branch is checked out, never re-created, so --orphan is
        # not a way to discard one: both precedences are behaviour, not an
        # accident of the order the cases happen to be written in.
        pytest.param(
            True,
            [],
            True,
            (['/repo/pub', 'pub'], ()),
            id='local-branch-outranks-orphan',
        ),
        pytest.param(
            False,
            ['origin'],
            True,
            (
                ['--track', '-b', 'pub', '/repo/pub', 'origin/pub'],
                ("creating 'pub' tracking origin/pub",),
            ),
            id='remote-branch-outranks-orphan',
        ),
    ],
)
def test_add_args_decides_the_four_shapes(
    monkeypatch,
    local,
    remotes,
    orphan,
    expected,
):
    decided = _decided(monkeypatch, local=local, remotes=remotes, orphan=orphan)
    assert decided == expected


def test_an_ambiguous_remote_is_refused_even_when_orphan_was_asked_for(monkeypatch):
    """--orphan does not route around the ambiguity; it is decided first.

    Orphaning would answer the request, and answer it by discarding the
    history on both remotes -- silently, since an orphan branch failing to
    contain anything is what an orphan branch is.
    """
    with pytest.raises(WorkshopifyError, match='origin, other'):
        _decided(monkeypatch, local=False, remotes=['origin', 'other'], orphan=True)


# --- prepare_worktree ------------------------------------------------------


def test_creates_a_new_branch_from_head(tmp_path):
    repo = git_repo(tmp_path / 'repo', {'a.txt': 'a'})
    wt = prepare_worktree(repo, 'pub', repo / 'pub', orphan=False)
    assert wt.path == (repo / 'pub').resolve()
    assert (wt.path / 'a.txt').is_file()
    # Advisory, not diagnostic: this is where a mistyped branch goes wrong.
    assert any('creating it from' in note for note in wt.notes)


def test_a_prepared_worktree_that_needed_no_explaining_says_nothing(tmp_path):
    repo = git_repo(tmp_path / 'repo', {'a.txt': 'a'})
    subprocess.run(['git', 'branch', 'pub'], cwd=repo, check=True)
    assert prepare_worktree(repo, 'pub', repo / 'pub', orphan=False).notes == ()


def test_reuses_an_existing_worktree_at_the_same_path(tmp_path):
    repo = git_repo(tmp_path / 'repo', {'a.txt': 'a'})
    first = prepare_worktree(repo, 'pub', repo / 'pub', orphan=False)
    again = prepare_worktree(repo, 'pub', repo / 'pub', orphan=False)
    assert again.path == first.path
    assert again.notes == (), 'reusing a worktree needs no explanation'


def test_refuses_the_branch_checked_out_elsewhere(tmp_path):
    repo = git_repo(tmp_path / 'repo', {'a.txt': 'a'})
    prepare_worktree(repo, 'pub', repo / 'pub', orphan=False)
    with pytest.raises(WorkshopifyError, match='already checked out'):
        prepare_worktree(repo, 'pub', repo / 'elsewhere', orphan=False)


def test_refuses_a_path_holding_another_branch(tmp_path):
    repo = git_repo(tmp_path / 'repo', {'a.txt': 'a'})
    prepare_worktree(repo, 'one', repo / 'wt', orphan=False)
    with pytest.raises(WorkshopifyError, match='already a worktree'):
        prepare_worktree(repo, 'two', repo / 'wt', orphan=False)


def test_refuses_a_path_holding_a_detached_worktree(tmp_path):
    """A detached checkout emits no branch line, but the path is taken anyway.

    Reading only branch lines lost the entry, and the collision was then
    reported as a directory that "is not empty" -- true, and about the wrong
    thing.
    """
    repo = git_repo(tmp_path / 'repo', {'a.txt': 'a'})
    subprocess.run(
        ['git', 'worktree', 'add', '--detach', str(repo / 'wt')],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    with pytest.raises(WorkshopifyError, match='no branch checked out'):
        prepare_worktree(repo, 'two', repo / 'wt', orphan=False)


def test_refuses_a_target_that_is_a_file(tmp_path):
    """The default target is a name in the repo root; a repo may already use it."""
    repo = git_repo(tmp_path / 'repo', {'a.txt': 'a'})
    (repo / 'workshop').write_text('a file, not a worktree')
    with pytest.raises(WorkshopifyError, match='not a directory'):
        prepare_worktree(repo, 'workshop', repo / 'workshop', orphan=False)


def test_refuses_a_nonempty_directory(tmp_path):
    repo = git_repo(tmp_path / 'repo', {'a.txt': 'a'})
    (repo / 'occupied').mkdir()
    (repo / 'occupied' / 'x').write_text('x')
    with pytest.raises(WorkshopifyError, match='not empty'):
        prepare_worktree(repo, 'pub', repo / 'occupied', orphan=False)


def test_tracks_a_remote_branch_on_a_fresh_clone(tmp_path):
    origin = git_repo(tmp_path / 'origin_repo', {'a.txt': 'a'})
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


def test_refuses_a_branch_that_two_remotes_both_have(tmp_path):
    """Picking a remote here would silently publish against the wrong history."""
    origin = git_repo(tmp_path / 'origin_repo', {'a.txt': 'a'})
    other = git_repo(tmp_path / 'other_repo', {'a.txt': 'a'})
    for repo in (origin, other):
        subprocess.run(['git', 'branch', 'pub'], cwd=repo, check=True)
    clone = tmp_path / 'clone'
    subprocess.run(['git', 'clone', '-q', str(origin), str(clone)], check=True)
    subprocess.run(['git', 'remote', 'add', 'other', str(other)], cwd=clone, check=True)
    subprocess.run(['git', 'fetch', '-q', 'other'], cwd=clone, check=True)
    with pytest.raises(WorkshopifyError, match='origin, other'):
        prepare_worktree(clone, 'pub', clone / 'pub', orphan=False)


def test_orphan_branch_has_no_history(tmp_path):
    repo = git_repo(tmp_path / 'repo', {'a.txt': 'a'})
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
    repo = git_repo(tmp_path / 'repo', {'a.txt': 'a'})
    wt = prepare_worktree(repo, 'pub', repo / 'pub', orphan=False)
    shutil.rmtree(wt.path)
    # The stale registration would otherwise block re-adding the branch.
    assert prepare_worktree(repo, 'pub', repo / 'pub', orphan=False).path == wt.path


# --- is_dirty / clean ------------------------------------------------------


def test_is_dirty_detects_modified_and_untracked(tmp_path):
    wt = git_repo(tmp_path / 'wt')
    (wt / 'a.txt').write_text('a')
    commit_all(wt)
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
    commit_all(wt)
    (wt / 'ignored.txt').write_text('i')
    (wt / '.gitignore').write_text('ignored.txt\n')
    clean(wt)
    assert not (wt / 'tracked.txt').exists()
    assert (wt / 'ignored.txt').exists(), 'ignored files must survive'


def test_clean_of_a_worktree_with_nothing_tracked_is_a_no_op(tmp_path):
    """Why `git rm` is passed --ignore-unmatch: the first build has nothing yet.

    An orphan publish branch tracks no files at all, and `git rm .` against a
    pathspec matching nothing exits 1 -- so --clean would fail on exactly the
    build that has least to clean.
    """
    repo = git_repo(tmp_path / 'repo', {'a.txt': 'a'})
    wt = prepare_worktree(repo, 'pages', repo / 'pages', orphan=True)
    clean(wt.path)
    assert not is_dirty(wt.path)
