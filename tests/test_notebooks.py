import json

from workshopify import context, notebooks
from workshopify.config import Config


def _setup(fixture_repo):
    config = Config.load(fixture_repo)
    return config, context.load(config)


def test_generate_writes_completed_exercise_and_notes(fixture_repo, tmp_path):
    config, ctx = _setup(fixture_repo)
    out = tmp_path / 'out'
    written = notebooks.generate(config, ctx, out)
    names = {str(p.relative_to(out.resolve())) for p in written}
    assert names == {
        'notebooks/completed/01_example.ipynb',
        'notebooks/01_example.ipynb',
        'notes/01_example.md',
    }
    for p in written:
        assert p.is_file()


def test_generate_is_byte_identical_across_runs(fixture_repo, tmp_path):
    config, ctx = _setup(fixture_repo)
    a, b = tmp_path / 'a', tmp_path / 'b'
    notebooks.generate(config, ctx, a)
    notebooks.generate(config, ctx, b)
    rel = 'notebooks/completed/01_example.ipynb'
    assert (a / rel).read_bytes() == (b / rel).read_bytes()


def test_cell_ids_are_positional_and_nbformat_legal(fixture_repo, tmp_path):
    config, ctx = _setup(fixture_repo)
    written = notebooks.generate(config, ctx, tmp_path / 'out')
    completed = next(p for p in written if 'completed' in str(p))
    cells = json.loads(completed.read_text())['cells']
    assert [c['id'] for c in cells][:2] == ['01_example-000', '01_example-001']
    for cell in cells:
        assert 1 <= len(cell['id']) <= 64
        assert all(ch.isalnum() or ch in '-_' for ch in cell['id'])


def test_no_templating_reaches_any_notebook(fixture_repo, tmp_path):
    config, ctx = _setup(fixture_repo)
    for p in notebooks.generate(config, ctx, tmp_path / 'out'):
        assert '[[[' not in p.read_text()


def test_generate_returns_exactly_the_files_it_wrote(fixture_repo, tmp_path):
    """The build subtracts this list from tracked files to flag cruft."""
    out = (tmp_path / 'out').resolve()
    stale = out / 'notebooks' / '99_renamed-away.ipynb'
    stale.parent.mkdir(parents=True)
    stale.write_text('stale')
    config, ctx = _setup(fixture_repo)
    written = notebooks.generate(config, ctx, out)
    assert written, 'nothing was generated'
    assert all(p.is_absolute() for p in written)
    on_disk = {p for p in out.rglob('*') if p.is_file()}
    assert set(written) == on_disk - {stale}


def test_generate_writes_nothing_outside_the_output_dir(fixture_repo, tmp_path):
    """The scrubber's configured paths are absolute, and name the source repo.

    Rebasing them onto output_dir is what keeps a build out of the working
    tree; get it wrong and every generated file lands next to its source, with
    the run still reporting success. An unchanged source repo is the only
    symptom there is.
    """
    config, ctx = _setup(fixture_repo)
    before = set(fixture_repo.rglob('*'))
    notebooks.generate(config, ctx, tmp_path / 'out')
    assert set(fixture_repo.rglob('*')) == before


def test_a_notes_file_with_no_notes_is_not_written_or_reported(
    fixture_repo,
    tmp_path,
):
    src = fixture_repo / 'src' / '01_example.py'
    # The whole marker line goes: a `#| scrub-note:` left without its id is an
    # error, not an absent note.
    kept = [
        line
        for line in src.read_text().splitlines(keepends=True)
        if 'scrub-note' not in line
    ]
    src.write_text(''.join(kept))
    config, ctx = _setup(fixture_repo)
    out = tmp_path / 'out'
    written = notebooks.generate(config, ctx, out)
    assert not (out / 'notes' / '01_example.md').exists()
    assert all('notes' not in str(p.relative_to(out.resolve())) for p in written)


def test_missing_source_file_diagnoses(fixture_repo, tmp_path):
    import pytest

    from workshopify.errors import WorkshopifyError

    (fixture_repo / 'src' / '01_example.py').rename(
        fixture_repo / 'src' / '02_other.py',
    )
    config, ctx = _setup(fixture_repo)
    with pytest.raises(WorkshopifyError, match='missing source file'):
        notebooks.generate(config, ctx, tmp_path / 'out')


def test_a_configured_path_outside_the_repo_diagnoses(fixture_repo, tmp_path):
    """There is nowhere under output_dir to put it, and no traceback for it."""
    import pytest

    from workshopify.errors import WorkshopifyError

    pyproject = fixture_repo / 'pyproject.toml'
    pyproject.write_text(
        pyproject.read_text().replace(
            'output = "notebooks/01_example.ipynb"',
            'output = "../elsewhere/01_example.ipynb"',
        ),
    )
    config, ctx = _setup(fixture_repo)
    with pytest.raises(WorkshopifyError, match='outside the repo'):
        notebooks.generate(config, ctx, tmp_path / 'out')


def test_a_bad_marker_diagnoses_against_the_source_file(fixture_repo, tmp_path):
    """0.5.0 rejects a malformed marker, and that must not reach cli as a traceback."""
    import pytest

    from workshopify.errors import WorkshopifyError

    src = fixture_repo / 'src' / '01_example.py'
    # A marker without its colon: an error in the scrubber, and the likeliest
    # thing an author gets wrong.
    src.write_text(
        src.read_text().replace('#| scrub-note: example-note', '#| scrub-note')
    )
    config, ctx = _setup(fixture_repo)
    with pytest.raises(WorkshopifyError, match=r'01_example\.py'):
        notebooks.generate(config, ctx, tmp_path / 'out')


def test_cleared_cells_carry_the_configured_clear_text(fixture_repo, tmp_path):
    """What a participant is left to type into is the point of the whole tool.

    The fixture configures an empty clear-text, so a cleared cell keeps only
    its "(See notes: ...)" reference. A scrubber upgrade that reinterprets an
    empty value would otherwise slip a placeholder into every exercise cell
    with nothing failing.
    """
    config, ctx = _setup(fixture_repo)
    out = tmp_path / 'out'
    notebooks.generate(config, ctx, out)
    exercise = json.loads((out / 'notebooks' / '01_example.ipynb').read_text())
    sources = [''.join(cell['source']) for cell in exercise['cells']]
    assert '# (See notes: example-note)' in sources
    assert not any('TODO: Implement this' in source for source in sources)
