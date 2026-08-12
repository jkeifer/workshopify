import pytest

from conftest import rewrite

from workshopify import context
from workshopify.config import Config
from workshopify.errors import WorkshopifyError


def test_namespace_carries_module_level_names(fixture_repo):
    ctx = context.load(Config.load(fixture_repo))
    assert ctx.namespace['word'] == 'hello'
    assert ctx.namespace['shout']() == 'HELLO'


def test_params_are_injected(fixture_repo):
    rewrite(fixture_repo, 'greeting = "hello"', 'greeting = "kia ora"')
    ctx = context.load(Config.load(fixture_repo))
    assert ctx.namespace['word'] == 'kia ora'


def test_assets_resolve_against_the_root(fixture_repo):
    ctx = context.load(Config.load(fixture_repo))
    assert ctx.assets == {
        (fixture_repo / 'notebooks/assets/logo.png').resolve(): (
            fixture_repo / 'assets/logo.png'
        ).resolve(),
    }


def test_an_absolute_asset_target_inside_the_root_is_kept(fixture_repo, tmp_path):
    """`root / target` is `target` itself when it is absolute, which is fine.

    What a target must be is inside the repo, not relative; an author who
    spells one out in full has said the same thing the long way.
    """
    target = fixture_repo / 'notebooks' / 'assets' / 'logo.png'
    source = tmp_path / 'source' / 'logo.png'
    (fixture_repo / 'context.py').write_text(
        f'word = "x"\nASSETS = {{{str(target)!r}: {str(source)!r}}}\n',
    )
    ctx = context.load(Config.load(fixture_repo))
    assert ctx.assets == {target.resolve(): source.resolve()}


def test_an_asset_target_outside_the_repo_root_is_rejected(fixture_repo, tmp_path):
    """A target names where the file lands in the published tree.

    Outside the repo there is no such place: build cannot rebase it under the
    worktree, so a repo declaring one renders and checks clean forever while
    never being publishable. Refused here, where the author's own spelling of
    it is still on screen.
    """
    target = tmp_path / 'elsewhere' / 'logo.png'
    (fixture_repo / 'context.py').write_text(
        f'word = "x"\nASSETS = {{{str(target)!r}: "assets/logo.png"}}\n',
    )
    with pytest.raises(WorkshopifyError, match='outside the repo root'):
        context.load(Config.load(fixture_repo))


def test_a_target_declared_twice_is_rejected(fixture_repo):
    """Two spellings of one target are two answers to a one-answer question.

    A dict keeps the last, so the asset built is whichever declaration came
    second -- and check() then verifies the target against that source and
    passes, with the other declaration doing nothing and saying nothing.
    """
    (fixture_repo / 'context.py').write_text(
        "word = 'x'\n"
        'ASSETS = {\n'
        "    'notebooks/assets/logo.png': 'assets/logo.png',\n"
        "    './notebooks/assets/logo.png': 'assets/other.png',\n"
        '}\n',
    )
    with pytest.raises(WorkshopifyError, match='twice'):
        context.load(Config.load(fixture_repo))


def test_sibling_imports_work(fixture_repo):
    (fixture_repo / 'helper.py').write_text('VALUE = 42\n')
    (fixture_repo / 'context.py').write_text(
        'from helper import VALUE\nword = str(VALUE)\n',
    )
    ctx = context.load(Config.load(fixture_repo))
    assert ctx.namespace['word'] == '42'


def test_missing_context_module_diagnoses(fixture_repo):
    (fixture_repo / 'context.py').unlink()
    with pytest.raises(WorkshopifyError, match='context module'):
        context.load(Config.load(fixture_repo))


def test_a_workshopify_error_in_the_module_passes_through(fixture_repo):
    (fixture_repo / 'context.py').write_text(
        'from workshopify.errors import WorkshopifyError\n'
        "raise WorkshopifyError('error: no such location')\n",
    )
    with pytest.raises(WorkshopifyError, match='no such location'):
        context.load(Config.load(fixture_repo))


def test_an_arbitrary_crash_is_wrapped_with_the_module_named(fixture_repo):
    (fixture_repo / 'context.py').write_text('boom\n')
    with pytest.raises(WorkshopifyError, match=r'context\.py'):
        context.load(Config.load(fixture_repo))


def test_assets_must_be_a_dict(fixture_repo):
    (fixture_repo / 'context.py').write_text("word = 'x'\nASSETS = ['nope']\n")
    with pytest.raises(WorkshopifyError, match='ASSETS'):
        context.load(Config.load(fixture_repo))


def test_assets_entries_must_be_paths(fixture_repo):
    (fixture_repo / 'context.py').write_text("word = 'x'\nASSETS = {'a.png': 3}\n")
    with pytest.raises(WorkshopifyError, match='str or Path'):
        context.load(Config.load(fixture_repo))
