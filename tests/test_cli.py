import json
import subprocess

from conftest import git_repo, rewrite, uv_lock

from workshopify import build
from workshopify.cli import main


def test_render_applies_and_reports(fixture_repo, monkeypatch, capsys):
    monkeypatch.chdir(fixture_repo)
    assert main(['render']) == 0
    assert 'logo.png' in capsys.readouterr().err  # the asset was placed


def test_check_clean_after_render(fixture_repo, monkeypatch):
    monkeypatch.chdir(fixture_repo)
    main(['render'])
    assert main(['check']) == 0


def test_check_reports_staleness_nonzero(fixture_repo, monkeypatch, capsys):
    monkeypatch.chdir(fixture_repo)
    main(['render'])
    rewrite(fixture_repo, 'greeting = "hello"', 'greeting = "yo"')
    assert main(['check']) == 1
    assert 'stale' in capsys.readouterr().err


def test_set_and_check_roundtrip(fixture_repo, monkeypatch):
    monkeypatch.chdir(fixture_repo)
    assert main(['set', 'greeting', 'kia ora']) == 0
    assert main(['check']) == 0


def test_generate_into_a_directory(fixture_repo, monkeypatch, tmp_path):
    monkeypatch.chdir(fixture_repo)
    out = tmp_path / 'out'
    assert main(['generate', '--output-dir', str(out)]) == 0
    nb = out / 'notebooks' / '01_example.ipynb'
    assert json.loads(nb.read_text())['cells']


def test_generate_reports_each_written_file_exactly_once(
    fixture_repo,
    monkeypatch,
    capsys,
):
    """One write, one line, in one vocabulary and one path format.

    The CLI names every written file from the list `notebooks.generate`
    returns, and is the only thing that names it: a generating step that
    announced its own writes would name each file twice, once relative and
    once absolute.
    """
    monkeypatch.chdir(fixture_repo)
    assert main(['generate']) == 0
    assert capsys.readouterr().err.splitlines() == [
        'wrote notebooks/completed/01_example.ipynb',
        'wrote notebooks/01_example.ipynb',
        'wrote notes/01_example.md',
    ]


def test_a_diagnosed_failure_prints_and_exits_one(fixture_repo, monkeypatch, capsys):
    monkeypatch.chdir(fixture_repo)
    assert main(['set', 'nope', 'x']) == 1
    assert 'error:' in capsys.readouterr().err


def test_a_failure_from_cog_arrives_as_a_diagnostic_too(
    fixture_repo,
    monkeypatch,
    capsys,
):
    """The mistake an author actually makes: a generator that throws.

    It comes out of cog, not out of workshopify, so undiagnosed it would reach
    main() as something other than a WorkshopifyError and land on the author as
    a traceback through the package they were only using. Here at the CLI is
    where that distinction is visible at all: whatever raised it, the author
    gets one `error:` line naming the file they have to fix, and exit 1.
    """
    monkeypatch.chdir(fixture_repo)
    (fixture_repo / 'src' / '02_broken.py').write_text(
        '# %% [markdown]\n'
        '# # Broken\n'
        '#\n'
        '# <!--[[[cog md(no_such_name()) ]]]-->\n'
        '# <!--[[[end]]]-->\n',
    )
    assert main(['render']) == 1
    err = capsys.readouterr().err
    assert err.startswith('error:')
    assert '02_broken.py' in err


def test_worktree_subcommand(fixture_repo, monkeypatch, capsys):
    git_repo(fixture_repo)
    monkeypatch.chdir(fixture_repo)
    assert main(['worktree', 'pub']) == 0
    assert (fixture_repo / 'pub' / '.git').exists()
    captured = capsys.readouterr()
    assert captured.out.strip() == str((fixture_repo / 'pub').resolve())
    # The advisory note prepare_worktree produced must still reach the user.
    assert 'creating it from' in captured.err


def test_worktree_subcommand_passes_orphan_through(fixture_repo, monkeypatch, capsys):
    """--orphan must reach prepare_worktree: the branch gets no history."""
    git_repo(fixture_repo)
    monkeypatch.chdir(fixture_repo)
    assert main(['worktree', 'pages', '--orphan']) == 0
    capsys.readouterr()
    result = subprocess.run(
        ['git', 'rev-parse', 'HEAD'],
        cwd=fixture_repo / 'pages',
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, 'an orphan worktree starts with no commits'


def test_build_publishes_into_the_branch_worktree(fixture_repo, monkeypatch, capsys):
    git_repo(fixture_repo)
    uv_lock(fixture_repo)
    monkeypatch.chdir(fixture_repo)
    assert main(['build']) == 0
    assert (fixture_repo / 'workshop' / 'Dockerfile').is_file()
    assert (fixture_repo / 'workshop' / 'notebooks' / '01_example.ipynb').is_file()

    err = capsys.readouterr().err
    target = (fixture_repo / 'workshop').resolve()
    assert f'built into {target}' in err
    assert f'review with: git -C {target} status' in err
    assert f'  git -C {target} add -A' in err
    assert f'PREK_ALLOW_NO_CONFIG=1 git -C {target} commit' in err
    assert f'  git -C {target} push' in err
    assert 'git hooks are shared across all worktrees' in err


def test_build_reports_stale_files_after_the_publish_instructions(
    fixture_repo,
    monkeypatch,
    capsys,
):
    """The cruft report goes last, so it does not scroll off above the happy path.

    A renamed exercise still shipping its old file is the one failure this
    report exists to catch; it is worth nothing printed above five lines of
    git incantations.
    """
    git_repo(fixture_repo)
    monkeypatch.chdir(fixture_repo)
    # The worktree is branched off HEAD, so everything committed above is
    # tracked there and none of it is written by this stubbed build.
    monkeypatch.setattr(build, 'build', lambda config, ctx, staging: set())

    assert main(['build']) == 0

    err = capsys.readouterr().err
    assert 'not written by this build:' in err
    assert '  Dockerfile' in err
    assert 're-run with --clean to remove' in err
    assert err.index('publish with:') < err.index('not written by this build:')


def test_build_stays_quiet_about_cruft_when_there_is_none(
    fixture_repo,
    monkeypatch,
    capsys,
):
    """--clean untracks everything, so the report has nothing to say."""
    git_repo(fixture_repo)
    monkeypatch.chdir(fixture_repo)
    monkeypatch.setattr(build, 'build', lambda config, ctx, staging: set())

    assert main(['build', '--clean']) == 0

    err = capsys.readouterr().err
    assert 'built into' in err
    assert 'not written by this build:' not in err
