from __future__ import annotations

import shutil
import subprocess

from pathlib import Path

import pytest

from conftest import commit_all, git_repo, load_workshop, rewrite, uv_lock

from workshopify import build
from workshopify.context import Context
from workshopify.errors import WorkshopifyError


def _set_include(fixture_repo, *entries):
    """Rewrite [tool.workshopify.build] include to name exactly `entries`."""
    listed = ', '.join(f'"{entry}"' for entry in entries)
    rewrite(fixture_repo, 'include = ["Dockerfile"]', f'include = [{listed}]')


# --- build tree assembly ---------------------------------------------------


def test_build_writes_the_complete_tree(fixture_repo, tmp_path):
    uv_lock(fixture_repo)
    config, ctx = load_workshop(fixture_repo)
    written = build.build(config, ctx, tmp_path)
    relative = {str(p) for p in written}

    # from include
    assert 'Dockerfile' in relative
    # from static/
    assert 'README.md' in relative
    # from ctx.assets
    assert 'notebooks/assets/logo.png' in relative
    # generated
    assert 'notebooks/01_example.ipynb' in relative
    assert 'notebooks/completed/01_example.ipynb' in relative
    assert 'notes/01_example.md' in relative
    # derived
    assert 'pyproject.toml' in relative
    assert 'uv.lock' in relative

    for path in written:
        assert (tmp_path / path).is_file(), f'{path} reported but not written'


def _tree(root):
    """Every file under root, as paths relative to it."""
    return {p.relative_to(root) for p in root.rglob('*') if p.is_file()}


def _assert_same_tree(left, right):
    """Both trees hold the same paths, and each path the same bytes."""
    assert _tree(left) == _tree(right)
    for path in sorted(_tree(left)):
        assert (right / path).read_bytes() == (left / path).read_bytes(), path


def test_build_is_idempotent(fixture_repo, tmp_path):
    """Two builds of the same source must produce identical bytes.

    This is the property that makes a from-scratch publish reviewable: if
    it fails, every publish diff is churn and the review step is useless.
    """
    uv_lock(fixture_repo)
    config, ctx = load_workshop(fixture_repo)
    first, second = tmp_path / 'one', tmp_path / 'two'
    build.build(config, ctx, first)
    build.build(config, ctx, second)
    # Path sets first: walking only `first` would never look at a file that
    # exists in `second` alone.
    _assert_same_tree(first, second)


def test_rebuilding_into_the_same_directory_matches_a_fresh_build(
    fixture_repo,
    tmp_path,
):
    """The real publish rebuilds into the persistent workshop worktree.

    Building into an empty directory twice cannot catch what a reused one
    does, so this asserts a second build over the first is indistinguishable
    from a single build into a clean tree.
    """
    uv_lock(fixture_repo)
    config, ctx = load_workshop(fixture_repo)
    reused, fresh = tmp_path / 'reused', tmp_path / 'fresh'
    build.build(config, ctx, reused)
    build.build(config, ctx, reused)
    build.build(config, ctx, fresh)
    _assert_same_tree(fresh, reused)


def test_build_does_not_report_stale_files_as_written(fixture_repo, tmp_path):
    """`written` is what this build wrote, not what happens to be present.

    The publish subtracts `written` from the branch's tracked files to flag
    cruft. If a rebuild over a reused worktree re-reported leftovers as
    written -- which globbing the destination does -- a renamed exercise
    would keep shipping its old file, silently.
    """
    uv_lock(fixture_repo)
    config, ctx = load_workshop(fixture_repo)
    build.build(config, ctx, tmp_path)

    stale = [
        Path('notebooks/99_renamed-away.ipynb'),
        Path('notes/99_renamed-away.md'),
        Path('notebooks/assets/old_asset.png'),
        Path('orphan_at_root.txt'),
    ]
    for relative in stale:
        planted = tmp_path / relative
        planted.parent.mkdir(parents=True, exist_ok=True)
        planted.write_text('stale')

    written = build.build(config, ctx, tmp_path)
    for relative in stale:
        assert relative not in written, f'{relative} reported as written'


def test_build_diagnoses_an_asset_target_outside_the_repo_root(
    fixture_repo,
    tmp_path,
):
    """The same refusal context.load makes, for a Context from anywhere else.

    An escaping target cannot get past load(), so this is the guard standing
    behind it: build() rebases every asset target under staging, and a target
    with no place there must be a diagnostic rather than relative_to's bare
    ValueError.
    """
    config, ctx = load_workshop(fixture_repo)
    escaped = Context(
        namespace=ctx.namespace,
        assets={(tmp_path / 'outside.png').resolve(): fixture_repo / 'assets/logo.png'},
    )
    with pytest.raises(WorkshopifyError, match='outside'):
        build.build(config, escaped, tmp_path / 'staging')


# --- include entries -------------------------------------------------------


def test_a_directory_include_reports_every_file_it_writes(fixture_repo, tmp_path):
    """The tree arm of _pairs, and the symlinked directory that broke its report.

    A copy that follows a symlinked directory and a walk that does not recurse
    into one disagree by everything under `data/link`: those files were
    written and never reported, so `unwritten` called them cruft on every
    build and --clean could never clear them -- it deletes what the next build
    writes again, unreported. This is also the only test that copies a tree at
    all, which is the whole reason _pairs exists.
    """
    shared = fixture_repo / 'shared'
    shared.mkdir()
    (shared / 'inner.txt').write_text('inner')
    data = fixture_repo / 'data'
    (data / 'nested').mkdir(parents=True)
    (data / 'a.txt').write_text('a')
    (data / 'nested' / 'b.txt').write_text('b')
    (data / 'link').symlink_to('../shared')

    _set_include(fixture_repo, 'data')
    uv_lock(fixture_repo)
    config, ctx = load_workshop(fixture_repo)
    written = build.build(config, ctx, tmp_path)

    reported = {p for p in written if p.parts[0] == 'data'}
    assert reported == {
        Path('data/a.txt'),
        Path('data/nested/b.txt'),
        Path('data/link/inner.txt'),
    }
    # The destination holds real directories, so this walk sees what the
    # source walk could not.
    assert reported == {Path('data') / p for p in _tree(tmp_path / 'data')}


def test_a_broken_symlink_in_an_include_diagnoses(fixture_repo, tmp_path):
    """A dangling link is an OSError from the copy, not a traceback from cli."""
    data = fixture_repo / 'data'
    data.mkdir()
    (data / 'dangling').symlink_to('gone.txt')
    _set_include(fixture_repo, 'data')
    config, ctx = load_workshop(fixture_repo)
    with pytest.raises(WorkshopifyError, match='copying'):
        build.build(config, ctx, tmp_path)


def test_a_missing_include_entry_diagnoses(fixture_repo, tmp_path):
    """A typo'd entry ships a tree missing the file it named."""
    _set_include(fixture_repo, 'nope.txt')
    config, ctx = load_workshop(fixture_repo)
    with pytest.raises(WorkshopifyError, match='does not exist'):
        build.build(config, ctx, tmp_path)


def test_an_absolute_include_entry_diagnoses(fixture_repo, tmp_path):
    """`root / name` and `staging / name` are both `name` when it is absolute.

    Source and destination become one file: the copy is a SameFileError, and
    the relative_to that reports it has nothing to subtract.
    """
    _set_include(fixture_repo, str(fixture_repo / 'Dockerfile'))
    config, ctx = load_workshop(fixture_repo)
    with pytest.raises(WorkshopifyError, match='include entry'):
        build.build(config, ctx, tmp_path)


def test_an_include_entry_that_climbs_out_of_the_repo_diagnoses(
    fixture_repo,
    tmp_path,
):
    """PurePath normalizes nothing, so `..` is written, not caught.

    The build reads outside the repo and writes outside the worktree, while
    `relative_to(staging)` succeeds lexically and reports `../shared/...` as a
    tracked file no build wrote -- cruft that cannot be cleaned because it was
    never in the worktree.
    """
    outside = fixture_repo.parent / 'shared'
    outside.mkdir()
    (outside / 'x.txt').write_text('x')
    _set_include(fixture_repo, '../shared')
    config, ctx = load_workshop(fixture_repo)
    # A staging directory with a parent of its own, so `staging / '../shared'`
    # is somewhere the test can look for and not the source it came from.
    staging = tmp_path / 'stage' / 'wt'
    with pytest.raises(WorkshopifyError, match='include entry'):
        build.build(config, ctx, staging)
    assert not (staging.parent / 'shared').exists(), 'wrote outside the staging tree'


def test_every_copy_takes_its_mode_from_the_source(fixture_repo, tmp_path):
    """git records one bit of a file's mode, so a build must not vary it.

    copyfile gives a fresh destination the umask's mode and an existing one
    whatever mode it already had: setup.sh shipped executable from a directory
    include but not as a single-file include, a static/ overlay, or an asset
    -- and a rebuild into a reused worktree could give byte-identical content
    a different git mode than a fresh build, which comparing bytes cannot see.
    """
    sources = ['setup.sh', 'bin/run.sh', 'static/launch.sh', 'assets/tool.sh']
    (fixture_repo / 'bin').mkdir()
    for name in sources:
        script = fixture_repo / name
        script.write_text('#!/bin/sh\n')
        script.chmod(0o755)
    ctx_py = fixture_repo / 'context.py'
    ctx_py.write_text(
        ctx_py.read_text() + "ASSETS['tools/tool.sh'] = 'assets/tool.sh'\n",
    )
    _set_include(fixture_repo, 'setup.sh', 'bin')
    uv_lock(fixture_repo)
    config, ctx = load_workshop(fixture_repo)

    # One per way a file reaches the tree: a single-file include, a directory
    # include, the static/ overlay, and an asset.
    published = ['setup.sh', 'bin/run.sh', 'launch.sh', 'tools/tool.sh']
    build.build(config, ctx, tmp_path)
    for name in published:
        assert (tmp_path / name).stat().st_mode & 0o777 == 0o755, name

    for name in published:
        (tmp_path / name).chmod(0o600)
    build.build(config, ctx, tmp_path)
    for name in published:
        assert (tmp_path / name).stat().st_mode & 0o777 == 0o755, name


def test_two_sources_writing_one_path_are_refused(fixture_repo, tmp_path):
    """One published path, two things claiming it, and no way to have both.

    An ASSETS target that is also an include entry is two answers to a
    one-answer question, and what would ship is whichever step ran second.
    Both spellings are named, because reconciling them is the fix and neither
    one alone locates it.

    Nothing is written: the copies are planned before any is made, so a config
    error costs the author a message rather than a half-populated worktree --
    `publish_into` hands in the worktree they work in.
    """
    (fixture_repo / 'setup.sh').write_text('#!/bin/sh\n')
    ctx_py = fixture_repo / 'context.py'
    ctx_py.write_text(ctx_py.read_text() + "ASSETS['setup.sh'] = 'assets/logo.png'\n")
    _set_include(fixture_repo, 'setup.sh')
    uv_lock(fixture_repo)
    config, ctx = load_workshop(fixture_repo)
    staging = tmp_path / 'staging'
    with pytest.raises(WorkshopifyError) as e:
        build.build(config, ctx, staging)
    assert "include entry 'setup.sh'" in str(e.value)
    assert 'asset setup.sh' in str(e.value)
    assert not list(staging.rglob('*')), 'refused after writing part of the tree'


def test_the_same_include_entry_twice_is_not_a_collision(fixture_repo, tmp_path):
    """Repetition is not contradiction: both writes put the same bytes there.

    The check is for two *sources* disagreeing over one path, so a config
    that names one entry twice must still build -- there is nothing for an
    author to reconcile.
    """
    _set_include(fixture_repo, 'Dockerfile', 'Dockerfile')
    uv_lock(fixture_repo)
    config, ctx = load_workshop(fixture_repo)
    assert Path('Dockerfile') in build.build(config, ctx, tmp_path)


def test_overlapping_include_entries_are_not_a_collision(fixture_repo, tmp_path):
    """The same repetition wearing two spellings, which is still not a conflict.

    `docs` and `docs/index.md` both write `docs/index.md` -- the same bytes,
    from the same file, in the same mode. Identity is where the bytes come
    from, so comparing how the diagnostic names each one instead refuses a
    build that has exactly one answer and demands the author change it.
    """
    docs = fixture_repo / 'docs'
    docs.mkdir()
    (docs / 'index.md').write_text('hi')
    _set_include(fixture_repo, 'docs', 'docs/index.md')
    uv_lock(fixture_repo)
    config, ctx = load_workshop(fixture_repo)
    assert Path('docs/index.md') in build.build(config, ctx, tmp_path)
    assert (tmp_path / 'docs/index.md').read_text() == 'hi'


def test_an_include_entry_that_names_nothing_copies_nothing(fixture_repo, tmp_path):
    """The refusal comes before the first copy, not between two of them.

    Every include entry is resolved while the plan is being built, so the
    third one being wrong is caught with the first two still unwritten.
    """
    _set_include(fixture_repo, 'Dockerfile', 'nope')
    uv_lock(fixture_repo)
    config, ctx = load_workshop(fixture_repo)
    staging = tmp_path / 'staging'
    with pytest.raises(WorkshopifyError, match='does not exist'):
        build.build(config, ctx, staging)
    assert not (staging / 'Dockerfile').exists()


def test_a_repo_with_no_static_directory_builds(fixture_repo, tmp_path):
    """The overlay is optional; a repo without one is not a build failure."""
    shutil.rmtree(fixture_repo / 'static')
    uv_lock(fixture_repo)
    config, ctx = load_workshop(fixture_repo)
    written = build.build(config, ctx, tmp_path)
    assert Path('README.md') not in written
    assert not (tmp_path / 'README.md').exists()


# --- unwritten -------------------------------------------------------------


def test_unwritten_reports_tracked_files_the_build_did_not_write(tmp_path):
    wt = git_repo(tmp_path / 'wt')
    (wt / 'kept.txt').write_text('a')
    (wt / 'orphan.txt').write_text('b')
    commit_all(wt)
    stale = build.unwritten(wt, {Path('kept.txt')})
    assert [str(p) for p in stale] == ['orphan.txt']


def test_unwritten_matches_a_non_ascii_tracked_path(tmp_path):
    """core.quotePath escapes a non-ASCII path in ls-files output; -z does not.

    A quoted "caf\\303\\251.txt" can never equal a Path the build reports, so
    the file would be flagged as cruft on every publish and --clean could
    never resolve it -- the next build writes it back under its real name.
    """
    wt = git_repo(tmp_path / 'wt')
    (wt / 'café.txt').write_text('a')
    commit_all(wt)
    quoted = subprocess.run(
        ['git', 'ls-files'],
        cwd=wt,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert '\\303\\251' in quoted, 'core.quotePath is off; this proves nothing'

    assert build.unwritten(wt, {Path('café.txt')}) == []
    assert build.unwritten(wt, set()) == [Path('café.txt')]


def test_unwritten_ignores_untracked_files(tmp_path):
    wt = git_repo(tmp_path / 'wt')
    (wt / 'tracked.txt').write_text('a')
    commit_all(wt)
    (wt / 'scratch.txt').write_text('b')
    assert build.unwritten(wt, {Path('tracked.txt')}) == []


# --- publish_into ----------------------------------------------------------


def _recording_build(calls, names=('built.txt',)):
    """A stand-in for build() that records its calls and writes `names`.

    publish_into's job is orchestration -- guard, then clean, then build, then
    the cruft report -- and build() itself is covered thoroughly above. Faking
    it keeps these tests about that ordering, and fast.
    """

    def fake(config, ctx, staging):
        written = set()
        for name in names:
            path = Path(staging) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('built')
            written.add(Path(name))
        calls.append((config, ctx, Path(staging)))
        return written

    return fake


def test_publish_into_refuses_a_dirty_worktree(fixture_repo, tmp_path, monkeypatch):
    config, ctx = load_workshop(fixture_repo)
    wt = git_repo(tmp_path / 'wt')
    (wt / 'a.txt').write_text('a')
    commit_all(wt)
    (wt / 'a.txt').write_text('dirty')

    calls = []
    monkeypatch.setattr(build, 'build', _recording_build(calls))

    with pytest.raises(WorkshopifyError) as excinfo:
        build.publish_into(
            config,
            ctx,
            wt,
            clean_first=False,
            overwrite_dirty=False,
        )
    assert 'uncommitted changes' in str(excinfo.value)
    assert calls == [], 'the build must not run'
    assert (wt / 'a.txt').read_text() == 'dirty', 'the dirty file must survive'


def test_publish_into_refuses_a_dirty_worktree_in_clean_mode(
    fixture_repo,
    tmp_path,
    monkeypatch,
):
    """The guard covers --clean too, and is checked before clean() acts.

    Narrowing it to the default mode would leave --clean deleting notebook
    edits made in Jupyter that exist nowhere else yet.
    """
    config, ctx = load_workshop(fixture_repo)
    wt = git_repo(tmp_path / 'wt')
    (wt / 'a.txt').write_text('a')
    commit_all(wt)
    (wt / 'new.txt').write_text('n')

    calls = []
    monkeypatch.setattr(build, 'build', _recording_build(calls))

    with pytest.raises(WorkshopifyError) as excinfo:
        build.publish_into(
            config,
            ctx,
            wt,
            clean_first=True,
            overwrite_dirty=False,
        )
    assert 'uncommitted changes' in str(excinfo.value)
    assert calls == [], 'the build must not run'
    assert (wt / 'a.txt').exists(), 'clean() must not have run'
    assert (wt / 'new.txt').exists()


def test_publish_into_overwrite_dirty_bypasses_the_guard(
    fixture_repo,
    tmp_path,
    monkeypatch,
):
    config, ctx = load_workshop(fixture_repo)
    wt = git_repo(tmp_path / 'wt')
    (wt / 'a.txt').write_text('a')
    commit_all(wt)
    (wt / 'a.txt').write_text('dirty')

    calls = []
    monkeypatch.setattr(build, 'build', _recording_build(calls))

    published = build.publish_into(
        config,
        ctx,
        wt,
        clean_first=False,
        overwrite_dirty=True,
    )
    assert published.written == {Path('built.txt')}
    assert len(calls) == 1, 'the build must run'
    # The default build only writes. A dirty file it does not write survives,
    # which is why "--overwrite-dirty discards them" was the wrong wording.
    assert (wt / 'a.txt').read_text() == 'dirty'


def test_publish_into_clean_overwrite_dirty_removes_a_modified_tracked_file(
    fixture_repo,
    tmp_path,
    monkeypatch,
):
    """git rm refuses a locally modified file unless forced.

    This combination is the only way to reach clean() with a dirty worktree,
    since the guard blocks it otherwise -- so without -f the one documented
    escape hatch is exactly the broken one, and it fails with git's
    explanation thrown away.
    """
    config, ctx = load_workshop(fixture_repo)
    wt = git_repo(tmp_path / 'wt')
    (wt / 'a.txt').write_text('a')
    commit_all(wt)
    (wt / 'a.txt').write_text('locally modified')

    calls = []
    monkeypatch.setattr(build, 'build', _recording_build(calls))

    build.publish_into(
        config,
        ctx,
        wt,
        clean_first=True,
        overwrite_dirty=True,
    )
    assert not (wt / 'a.txt').exists(), 'the modified tracked file must be removed'
    assert (wt / 'built.txt').exists()


def test_publish_into_returns_a_tracked_file_the_build_did_not_write(
    fixture_repo,
    tmp_path,
    monkeypatch,
):
    """The cruft report is data, not output; the CLI decides how to show it."""
    config, ctx = load_workshop(fixture_repo)
    wt = git_repo(tmp_path / 'wt')
    (wt / 'orphan.txt').write_text('o')
    commit_all(wt)

    calls = []
    monkeypatch.setattr(build, 'build', _recording_build(calls))

    published = build.publish_into(
        config,
        ctx,
        wt,
        clean_first=False,
        overwrite_dirty=False,
    )
    assert published.written == {Path('built.txt')}
    assert published.stale == [Path('orphan.txt')]
