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

# A context whose namespace holds something a generator can mutate, and a
# source whose rendered value reports how many times it has been mutated. The
# rendered value is therefore constant only if every run gets its own copy.
MUTABLE_CONTEXT = 'SEEN = []\n'
MUTATING_SRC = """\
# %%
# <!--[[[cog SEEN.append(1); cog.outl(f"n = {len(SEEN)}") ]]]-->
# <!--[[[end]]]-->
"""

# The two ways cog itself fails: a generator that raises, and a block whose
# end marker is missing. Both are things a workshop author types.
FAILING_SRC = """\
# %%
# <!--[[[cog raise RuntimeError('boom') ]]]-->
# <!--[[[end]]]-->
"""
UNCLOSED_SRC = """\
# %%
# <!--[[[cog cog.outl('x = 1') ]]]-->
x = 1
"""

# Both routes to the process stdout: cog's own messages and a generator's
# print, alongside real output so the render still has to succeed.
CHATTY_SRC = """\
# %%
# <!--[[[cog print('leaked'); cog.msg('also leaked'); cog.outl('x = 1') ]]]-->
# <!--[[[end]]]-->
"""


def _sole_source(fixture_repo, context_text, src_text):
    """The fixture reduced to one context and one rendered file, both given."""
    (fixture_repo / 'context.py').write_text(context_text)
    (fixture_repo / 'src/01_example.py').write_text(src_text)
    return _setup(fixture_repo)


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


def test_check_reports_a_missing_asset_source(fixture_repo):
    # apply refuses outright; check has to keep going and say which asset it
    # could not even compare, naming the source rather than the target.
    config, ctx = _setup(fixture_repo)
    (fixture_repo / 'assets/logo.png').unlink()
    errors = render.check(config, ctx)
    assert any('missing; cannot check notebooks/assets/logo.png' in e for e in errors)


def test_apply_is_idempotent(fixture_repo):
    # The claim apply's docstring makes: a second run writes nothing at all,
    # so the tree -- contents and mtimes alike -- is untouched.
    config, ctx = _setup(fixture_repo)
    render.apply(config, ctx)
    before = _snapshot(fixture_repo)
    assert render.apply(config, ctx) == []
    assert _snapshot(fixture_repo) == before


def test_apply_is_idempotent_under_a_mutating_generator(fixture_repo):
    # Each run must render against its own copy of the namespace. Sharing it
    # would make the value a count of how many times apply had run, so the
    # second run would rewrite the file and check() after apply would disagree
    # with the apply that just ran.
    config, ctx = _sole_source(fixture_repo, MUTABLE_CONTEXT, MUTATING_SRC)
    src = fixture_repo / 'src/01_example.py'
    assert render.apply(config, ctx) == [src]
    assert 'n = 1' in src.read_text()
    assert render.apply(config, ctx) == []
    assert render.check(config, ctx) == []
    # And nothing the generator did survived into the caller's namespace.
    assert ctx.namespace['SEEN'] == []


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


def test_publish_rejects_output_that_is_not_a_jupytext_fixed_point(fixture_repo):
    _config, ctx = _setup(fixture_repo)
    # A generator whose output is a bare (uncommented) line inside a markdown
    # cell round-trips to something else, though the cell shape survives.
    bad = (
        '# %% [markdown]\n'
        '# heading\n'
        '#\n'
        "# <!--[[[cog cog.outl('naked line') ]]]-->\n"
        'naked line\n'
        '# <!--[[[end]]]-->\n'
    )
    with pytest.raises(render.PublishError, match='not a jupytext fixed point'):
        render.publish(ctx, bad, '<bad>')


def test_publish_rejects_a_generator_that_breaks_cell_structure(fixture_repo):
    _config, ctx = _setup(fixture_repo)
    # A cell whose only content is the generator: it reads as code in the
    # source and as nothing at all once the templating is gone, so a cell that
    # participants would see is not the cell the author wrote.
    bad = (
        '# %% [markdown]\n'
        '# heading\n'
        '\n'
        '# %%\n'
        "# <!--[[[cog cog.out('') ]]]-->\n"
        '# <!--[[[end]]]-->\n'
    )
    with pytest.raises(render.PublishError, match='cell structure changed'):
        render.publish(ctx, bad, '<bad>')


def test_publish_rejects_a_generator_that_emits_a_marker(fixture_repo):
    _config, ctx = _setup(fixture_repo)
    # The marker is assembled at generation time so cog sees no marker in the
    # source and strips happily; the strip is only sound if what it leaves is
    # checked, not if cog reports success.
    bad = (
        '# %%\n'
        "# <!--[[[cog cog.outl('# ' + chr(60) + '!--[[[cog x ]]' + ']-->') ]]]-->\n"
        '# <!--[[[end]]]-->\n'
    )
    with pytest.raises(render.PublishError, match='marker residue'):
        render.publish(ctx, bad, '<bad>')


def test_a_failing_generator_is_diagnosed_not_raised_raw(fixture_repo):
    # The most likely authoring mistake there is, and it reaches cli.main --
    # which catches only WorkshopifyError -- from every command.
    config, ctx = _sole_source(fixture_repo, MUTABLE_CONTEXT, FAILING_SRC)
    with pytest.raises(WorkshopifyError, match='RuntimeError: boom'):
        render.check(config, ctx)


def test_an_unclosed_marker_is_diagnosed_not_raised_raw(fixture_repo):
    config, ctx = _sole_source(fixture_repo, MUTABLE_CONTEXT, UNCLOSED_SRC)
    with pytest.raises(WorkshopifyError, match=r"Missing '<!--\[\[\[end\]\]\]-->'"):
        render.check(config, ctx)


def test_generator_chatter_never_reaches_stdout(fixture_repo, capsys):
    # stdout is `workshopify worktree`'s machine-readable result, so a print()
    # left in a generator must not land on it. cog binds its own stdout at
    # construction and a generator's print resolves sys.stdout as it runs, so
    # both routes are exercised here.
    config, ctx = _sole_source(fixture_repo, MUTABLE_CONTEXT, CHATTY_SRC)
    assert render.apply(config, ctx) == [fixture_repo / 'src/01_example.py']
    assert 'x = 1' in (fixture_repo / 'src/01_example.py').read_text()
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == ''


def test_no_files_matched_diagnoses(fixture_repo):
    (fixture_repo / 'src/01_example.py').unlink()
    config, ctx = _setup(fixture_repo)
    with pytest.raises(WorkshopifyError, match='matched nothing'):
        render.check(config, ctx)
