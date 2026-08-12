import shutil

from pathlib import Path

import pytest

from workshopify import context, render
from workshopify.config import Config
from workshopify.errors import WorkshopifyError

TWO_ASSETS = """\
ASSETS = {
    'notebooks/assets/logo.png': 'assets/logo.png',
    'notebooks/assets/second.png': 'assets/second.png',
}
"""


def _setup(fixture_repo):
    config = Config.load(fixture_repo)
    return config, context.load(config)


def _snapshot(root):
    """Every file in the tree by content and mtime, to prove nothing wrote."""
    return {
        p: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in sorted(root.rglob('*'))
        if p.is_file()
    }


def test_fresh_fixture_renders_clean(fixture_repo):
    # The fresh fixture's rendered values are already up to date; the only
    # thing not yet in place is the declared asset, which apply() copies.
    config, ctx = _setup(fixture_repo)
    assert not any('stale rendered values' in e for e in render.check(config, ctx))
    changed = render.apply(config, ctx)
    assert [p.name for p in changed] == ['logo.png']
    assert render.apply(config, ctx) == []


def test_stale_value_is_reported_and_applied(fixture_repo):
    pyproject = fixture_repo / 'pyproject.toml'
    pyproject.write_text(
        pyproject.read_text().replace('greeting = "hello"', 'greeting = "tena koe"'),
    )
    config, ctx = _setup(fixture_repo)
    errors = render.check(config, ctx)
    stale = [e for e in errors if 'stale rendered values' in e]
    assert len(stale) == 1
    assert 'src/01_example.py' in stale[0]
    changed = render.apply(config, ctx)
    assert '01_example.py' in [p.name for p in changed]
    assert 'word = "tena koe"' in (fixture_repo / 'src/01_example.py').read_text()
    assert '# TENA KOE' in (fixture_repo / 'src/01_example.py').read_text()
    assert render.check(config, ctx) == []


def test_missing_asset_is_reported_and_applied(fixture_repo):
    config, ctx = _setup(fixture_repo)
    target = fixture_repo / 'notebooks/assets/logo.png'
    assert not target.exists()
    # check() reports it while the rendered text is otherwise clean
    # (fresh fixture has no notebooks/assets yet)
    errors = render.check(config, ctx)
    assert any('logo.png' in e for e in errors)
    changed = render.apply(config, ctx)
    assert target.read_bytes() == (fixture_repo / 'assets/logo.png').read_bytes()
    assert target in changed
    assert render.check(config, ctx) == []


def test_stale_asset_is_replaced(fixture_repo):
    config, ctx = _setup(fixture_repo)
    render.apply(config, ctx)
    target = fixture_repo / 'notebooks/assets/logo.png'
    target.write_bytes(b'stale')
    assert any('logo.png' in e for e in render.check(config, ctx))
    render.apply(config, ctx)
    assert target.read_bytes() == (fixture_repo / 'assets/logo.png').read_bytes()


def test_apply_refuses_a_missing_asset_source_before_writing(fixture_repo):
    (fixture_repo / 'assets/logo.png').unlink()
    config, ctx = _setup(fixture_repo)
    with pytest.raises(WorkshopifyError, match=r'logo\.png'):
        render.apply(config, ctx)


def test_apply_is_idempotent(fixture_repo):
    # The claim apply's docstring makes: a second run writes nothing at all,
    # so the tree -- contents and mtimes alike -- is untouched.
    config, ctx = _setup(fixture_repo)
    render.apply(config, ctx)
    before = _snapshot(fixture_repo)
    assert render.apply(config, ctx) == []
    assert _snapshot(fixture_repo) == before


def test_apply_converges_after_a_partial_asset_failure(fixture_repo, monkeypatch):
    # The copies are sequential, so a failure partway leaves the earlier ones
    # applied -- and re-running apply once the cause is cleared copies only
    # the remainder.
    (fixture_repo / 'assets/second.png').write_bytes(b'\x89PNG second')
    context_py = fixture_repo / 'context.py'
    context_py.write_text(
        context_py.read_text().split('ASSETS = ')[0] + TWO_ASSETS,
    )
    config, ctx = _setup(fixture_repo)
    logo = fixture_repo / 'notebooks/assets/logo.png'
    second = fixture_repo / 'notebooks/assets/second.png'
    copyfile = shutil.copyfile

    def fail_on_second(source, target, **kwargs):
        if Path(target).name == 'second.png':
            raise PermissionError(target)
        return copyfile(source, target, **kwargs)

    monkeypatch.setattr(render.shutil, 'copyfile', fail_on_second)
    with pytest.raises(PermissionError):
        render.apply(config, ctx)
    assert logo.is_file()
    assert not second.exists()

    monkeypatch.undo()
    assert render.apply(config, ctx) == [second]
    assert render.check(config, ctx) == []
    assert render.apply(config, ctx) == []


def test_publish_strips_markers_and_generators(fixture_repo):
    _config, ctx = _setup(fixture_repo)
    src = (fixture_repo / 'src/01_example.py').read_text()
    stripped = render.publish(ctx, src, 'src/01_example.py')
    assert '[[[' not in stripped
    assert 'word = "hello"' in stripped
    assert '# HELLO' in stripped


def test_publish_rejects_a_generator_that_breaks_cell_structure(fixture_repo):
    _config, ctx = _setup(fixture_repo)
    # A generator whose output is a bare (uncommented) line inside a markdown
    # cell is not a jupytext fixed point.
    bad = (
        '# %% [markdown]\n'
        '# heading\n'
        '#\n'
        "# <!--[[[cog cog.outl('naked line') ]]]-->\n"
        'naked line\n'
        '# <!--[[[end]]]-->\n'
    )
    with pytest.raises(render.PublishError):
        render.publish(ctx, bad, '<bad>')


def test_no_files_matched_diagnoses(fixture_repo):
    (fixture_repo / 'src/01_example.py').unlink()
    config, ctx = _setup(fixture_repo)
    with pytest.raises(WorkshopifyError, match='matched nothing'):
        render.check(config, ctx)
