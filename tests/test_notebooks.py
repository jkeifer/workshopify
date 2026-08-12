import json
import re

import nbformat
import pytest

from workshopify import context, notebooks
from workshopify.config import Config
from workshopify.errors import WorkshopifyError

# nbformat's own rule for a cell id, restated so a test failure names it.
CELL_ID = re.compile(r'^[a-zA-Z0-9-_]{1,64}$')


def _setup(fixture_repo):
    config = Config.load(fixture_repo)
    return config, context.load(config)


def _restem(fixture_repo, stem):
    """Rename the fixture's one notebook, in pyproject and in src/, to `stem`."""
    pyproject = fixture_repo / 'pyproject.toml'
    pyproject.write_text(pyproject.read_text().replace('01_example', stem))
    (fixture_repo / 'src' / '01_example.py').rename(
        fixture_repo / 'src' / f'{stem}.py',
    )


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
        assert CELL_ID.match(cell['id']), cell['id']


@pytest.mark.parametrize(
    'stem',
    [
        pytest.param('01.example', id='punctuation'),
        pytest.param('01 example', id='space'),
        pytest.param('01_éxample', id='non-ascii'),
        pytest.param('exercise_' + 'x' * 70, id='too-long'),
    ],
)
def test_a_stem_nbformat_would_reject_still_mints_valid_ids(
    stem,
    fixture_repo,
    tmp_path,
):
    """A filename obeys neither half of nbformat's rule for a cell id.

    `01.example`, a space, an accent, and any stem past 64 characters each
    produced an id that fails validation -- with the notebook written and the
    run reporting success, so the first thing to say so is whatever opens the
    published file.
    """
    _restem(fixture_repo, stem)
    config, ctx = _setup(fixture_repo)
    for path in notebooks.generate(config, ctx, tmp_path / 'out'):
        if path.suffix != '.ipynb':
            continue
        nbformat.validate(nbformat.read(path, as_version=4))


def test_id_stems_are_legal_deterministic_and_collision_free():
    """Sanitizing folds distinct stems together; the digest is what unfolds them.

    `01.example` and `01-example` differ only in a character the charset
    forbids, and truncation makes any two long stems agree on their head --
    either way two notebooks would share an id space and a merged notebook
    would hold one id twice. Positional ids are only worth having if they
    stay a pure function of the file they came from.
    """
    stems = [
        '01_example',
        '01.example',
        '01-example',
        '01 example',
        '01_éxample',
        'exercise_' + 'x' * 70,
        'exercise_' + 'x' * 71,
    ]
    ids = [notebooks._id_stem(stem) for stem in stems]
    for stem, id_stem in zip(stems, ids, strict=True):
        assert CELL_ID.match(f'{id_stem}-000'), stem
        assert notebooks._id_stem(stem) == id_stem, 'not a function of the stem'
    assert len(set(ids)) == len(ids), ids


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
    (fixture_repo / 'src' / '01_example.py').rename(
        fixture_repo / 'src' / '02_other.py',
    )
    config, ctx = _setup(fixture_repo)
    with pytest.raises(WorkshopifyError, match='missing source file'):
        notebooks.generate(config, ctx, tmp_path / 'out')


def test_a_configured_path_outside_the_repo_diagnoses(fixture_repo, tmp_path):
    """There is nowhere under output_dir to put it, and no traceback for it."""
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


def test_notes_with_nowhere_to_go_diagnoses(fixture_repo, tmp_path):
    """The notebook has noted cells and its entry configures no notes-file.

    The scrubber renders the notes and hands them over regardless, so saying
    nothing here ships an exercise whose cleared cells point at a "(See
    notes: ...)" document that was never written.
    """
    pyproject = fixture_repo / 'pyproject.toml'
    pyproject.write_text(
        pyproject.read_text().replace('notes-file = "notes/01_example.md"\n', ''),
    )
    config, ctx = _setup(fixture_repo)
    with pytest.raises(WorkshopifyError, match='no notes-file is configured'):
        notebooks.generate(config, ctx, tmp_path / 'out')


def test_a_malformed_scrubber_section_diagnoses(fixture_repo, tmp_path):
    """[tool.ipynb-scrubber] is config this module reads but does not own.

    ProjectConfig raises its own error type for a section it will not accept,
    and the author who typo'd the key is owed the sentence naming it rather
    than a traceback out of cli.
    """
    pyproject = fixture_repo / 'pyproject.toml'
    pyproject.write_text(
        pyproject.read_text().replace(
            '[tool.ipynb-scrubber.options]',
            '[tool.ipynb-scrubber]\nfliles = []\n\n[tool.ipynb-scrubber.options]',
        ),
    )
    config, ctx = _setup(fixture_repo)
    with pytest.raises(WorkshopifyError, match='fliles'):
        notebooks.generate(config, ctx, tmp_path / 'out')


def test_a_bad_marker_diagnoses_against_the_source_file(fixture_repo, tmp_path):
    """0.5.0 rejects a malformed marker, and that must not reach cli as a traceback."""
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
