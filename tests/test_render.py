import os
import shutil

from pathlib import Path

import pytest

from conftest import load_workshop, rewrite

from workshopify import fs, render
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
    return load_workshop(fixture_repo)


def _snapshot(root):
    """Every file in the tree by content and mtime, to prove nothing wrote."""
    return {
        p: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in sorted(root.rglob('*'))
        if p.is_file()
    }


def test_a_workshop_with_no_context_has_nothing_to_render(fixture_repo):
    """Neither half of render has any work, and neither calls that an error.

    The two halves are configured in different places -- rendered files in
    pyproject, assets in the context module -- so a workshop that uses neither
    has to be silent in both, not just in the one it declared away.
    """
    rewrite(fixture_repo, 'context = "context.py"\n', '')
    rewrite(fixture_repo, 'files = ["src/*.py"]', 'files = []')
    config, ctx = load_workshop(fixture_repo)
    assert render.check(config, ctx) == []
    assert render.apply(config, ctx) == []


def test_fresh_fixture_renders_clean(fixture_repo):
    # The fresh fixture's rendered values are already up to date; the only
    # thing not yet in place is the declared asset, which apply() copies.
    config, ctx = load_workshop(fixture_repo)
    assert not any('stale rendered values' in e for e in render.check(config, ctx))
    changed = render.apply(config, ctx)
    assert [p.name for p in changed] == ['logo.png']
    assert render.apply(config, ctx) == []


def test_stale_value_is_reported_and_applied(fixture_repo):
    rewrite(fixture_repo, 'greeting = "hello"', 'greeting = "tena koe"')
    config, ctx = load_workshop(fixture_repo)
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
    config, ctx = load_workshop(fixture_repo)
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
    config, ctx = load_workshop(fixture_repo)
    render.apply(config, ctx)
    target = fixture_repo / 'notebooks/assets/logo.png'
    target.write_bytes(b'stale')
    assert any('logo.png' in e for e in render.check(config, ctx))
    render.apply(config, ctx)
    assert target.read_bytes() == (fixture_repo / 'assets/logo.png').read_bytes()


def test_apply_refuses_a_missing_asset_source_before_writing(fixture_repo):
    # The param is rewritten first so there is genuinely a write pending: what
    # is asserted is the ordering, that apply asks assets for its verdict
    # before it writes the rendered text, and against an unchanged tree apply
    # would write nothing either way.
    rewrite(fixture_repo, 'greeting = "hello"', 'greeting = "tena koe"')
    (fixture_repo / 'assets/logo.png').unlink()
    config, ctx = load_workshop(fixture_repo)
    before = _snapshot(fixture_repo)
    with pytest.raises(WorkshopifyError, match=r'logo\.png'):
        render.apply(config, ctx)
    assert _snapshot(fixture_repo) == before


def test_check_reports_a_missing_asset_source(fixture_repo):
    # apply refuses outright; check has to keep going and say which asset it
    # could not even compare, naming the source rather than the target.
    config, ctx = load_workshop(fixture_repo)
    (fixture_repo / 'assets/logo.png').unlink()
    errors = render.check(config, ctx)
    assert any('missing; cannot check notebooks/assets/logo.png' in e for e in errors)


def test_check_can_be_asked_about_the_rendered_values_alone(fixture_repo):
    """Exactly one half is dropped, which takes both halves being wrong to see.

    Against a clean fixture the narrowed call returns nothing whatever it does
    with the rendered values, so it would pass while answering [] to every
    question. Both are broken here: the asset is uncopied in the fresh fixture
    and the param is rewritten out from under the rendered file, so the two
    diagnostics are told apart by which survives.
    """
    rewrite(fixture_repo, 'greeting = "hello"', 'greeting = "tena koe"')
    config, ctx = load_workshop(fixture_repo)
    both = render.check(config, ctx)
    assert any('logo.png' in e for e in both)
    assert any('stale rendered values' in e for e in both)

    narrowed = render.check(config, ctx, assets_too=False)
    assert not any('logo.png' in e for e in narrowed)
    assert [e.splitlines()[0] for e in narrowed] == [
        'src/01_example.py: stale rendered values',
    ]


def test_apply_is_idempotent(fixture_repo):
    # The claim apply's docstring makes: a second run writes nothing at all,
    # so the tree -- contents and mtimes alike -- is untouched.
    config, ctx = load_workshop(fixture_repo)
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
    config, ctx = load_workshop(fixture_repo)
    logo = fixture_repo / 'notebooks/assets/logo.png'
    second = fixture_repo / 'notebooks/assets/second.png'
    copy = shutil.copy

    def fail_on_second(source, target, **kwargs):
        if Path(target).name == 'second.png':
            raise PermissionError(target)
        return copy(source, target, **kwargs)

    monkeypatch.setattr(fs.shutil, 'copy', fail_on_second)
    # Diagnosed rather than a bare PermissionError: the copy goes through the
    # filesystem boundary, and nothing below cli.py raises a foreign error.
    with pytest.raises(WorkshopifyError, match=r'second\.png'):
        render.apply(config, ctx)
    assert logo.is_file()
    assert not second.exists()

    monkeypatch.undo()
    assert render.apply(config, ctx) == [second]
    assert render.check(config, ctx) == []
    assert render.apply(config, ctx) == []


def test_publish_strips_markers_and_generators(fixture_repo):
    _config, ctx = load_workshop(fixture_repo)
    src = (fixture_repo / 'src/01_example.py').read_text()
    stripped = render.publish(ctx, src, 'src/01_example.py')
    assert '[[[' not in stripped
    assert 'word = "hello"' in stripped
    assert '# HELLO' in stripped


def test_publish_rejects_output_that_is_not_a_jupytext_fixed_point(fixture_repo):
    _config, ctx = load_workshop(fixture_repo)
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
    _config, ctx = load_workshop(fixture_repo)
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
    _config, ctx = load_workshop(fixture_repo)
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
    config, ctx = load_workshop(fixture_repo)
    with pytest.raises(WorkshopifyError, match='matched nothing'):
        render.check(config, ctx)


def test_apply_copies_an_asset_with_the_source_mode(fixture_repo):
    """The repo's own copy of an asset must carry the source's mode.

    `assets._problem` compares bytes only, so a mode divergence in the repo's
    own copy is invisible to `check` and never self-heals: apply copies only
    when bytes differ. Taking the source's mode on the way in is the one thing
    that keeps the checked-in file right.

    The published tree is safe either way -- `build` re-copies each asset from
    its *source*, never from the copy apply wrote, and
    `test_every_copy_takes_its_mode_from_the_source` already pins that leg. It
    is the checked-in file this pins.
    """
    script = fixture_repo / 'assets/tool.sh'
    script.write_text('#!/bin/sh\n')
    script.chmod(0o755)
    context_py = fixture_repo / 'context.py'
    context_py.write_text(
        context_py.read_text() + "ASSETS['tools/tool.sh'] = 'assets/tool.sh'\n",
    )
    config, ctx = load_workshop(fixture_repo)

    copied = fixture_repo / 'tools/tool.sh'
    assert copied in render.apply(config, ctx)
    assert copied.stat().st_mode & 0o777 == 0o755


def test_an_unreadable_source_is_diagnosed(fixture_repo):
    """A directory named `foo.py` matches `src/*.py`, and reading one is an
    IsADirectoryError -- diagnosed, like every other filesystem failure."""
    (fixture_repo / 'src/broken.py').mkdir()
    config, ctx = load_workshop(fixture_repo)
    with pytest.raises(WorkshopifyError, match=r'reading .*broken\.py'):
        render.check(config, ctx)


def test_a_source_that_is_not_utf8_is_diagnosed(fixture_repo):
    """Pinning the encoding is what makes this failure possible, so `fs` owns it.

    `UnicodeDecodeError` is a `ValueError`, which `diagnosed` does not catch
    by default -- the same trap as `TOMLDecodeError`. Without it the module
    that exists to abolish tracebacks at this boundary would raise the one it
    had just introduced, on every file matched by [tool.workshopify.render].
    """
    (fixture_repo / 'src/01_example.py').write_bytes(b'# %%\n# caf\xe9\n')
    config, ctx = load_workshop(fixture_repo)
    with pytest.raises(WorkshopifyError, match=r'reading .*01_example\.py'):
        render.check(config, ctx)


@pytest.mark.skipif(os.geteuid() == 0, reason='root writes a read-only file anyway')
def test_apply_diagnoses_a_write_it_cannot_make(fixture_repo):
    """apply's own docstring reasons about "a permissions error ... partway
    through" -- a failure it discusses in prose and must not deliver as a
    traceback.

    The param is changed first so there is genuinely something to write: apply
    writes only what differs from what it would write, so against an unchanged
    tree this would pass without ever reaching the write.
    """
    rewrite(fixture_repo, 'greeting = "hello"', 'greeting = "tena koe"')
    config, ctx = load_workshop(fixture_repo)
    src = fixture_repo / 'src/01_example.py'
    src.chmod(0o444)
    try:
        with pytest.raises(WorkshopifyError, match=r'writing .*01_example\.py'):
            render.apply(config, ctx)
    finally:
        src.chmod(0o644)
