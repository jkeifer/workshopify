from dataclasses import dataclass, fields
from pathlib import Path

import pytest

from workshopify import config as config_module
from workshopify.config import BuildConfig, Config, NotebooksConfig, RenderConfig
from workshopify.errors import WorkshopifyError


def rewrite(root: Path, old: str, new: str) -> None:
    """Edit the fixture repo's pyproject.toml in place."""
    pyproject = root / 'pyproject.toml'
    text = pyproject.read_text()
    assert old in text
    pyproject.write_text(text.replace(old, new))


def test_load_reads_the_tables(fixture_repo):
    config = Config.load(fixture_repo)
    assert config.root == fixture_repo.resolve()
    assert config.context == Path('context.py')
    assert config.render.files == ('src/*.py',)
    assert config.build.include == ('Dockerfile',)
    assert config.params == {'greeting': 'hello'}


def test_defaults(fixture_repo):
    config = Config.load(fixture_repo)
    assert config.build.branch == 'workshop'
    assert config.build.keep_tables == ('project', 'tool.uv')
    assert config.build.drop_keys == ()
    assert config.notebooks.src == 'src'
    assert config.pyproject == fixture_repo.resolve() / 'pyproject.toml'


def test_display_path_is_relative_inside_the_root_and_absolute_outside(
    fixture_repo,
    tmp_path,
):
    """The one spelling of a path for a human, used by check() and the CLI alike.

    `generate --output-dir` writes outside the repo, so the fallback is not
    hypothetical: those paths have no relative spelling that means anything
    and must be named in full.
    """
    config = Config.load(fixture_repo)
    assert config.display_path(config.root / 'src' / '01_example.py') == (
        'src/01_example.py'
    )
    outside = (tmp_path / 'elsewhere' / 'out.ipynb').resolve()
    assert config.display_path(outside) == str(outside)


def test_load_without_table_diagnoses(tmp_path):
    (tmp_path / 'pyproject.toml').write_text('[project]\nname = "x"\n')
    with pytest.raises(WorkshopifyError, match=r'no \[tool\.workshopify\] table'):
        Config.load(tmp_path)


def test_load_without_pyproject_diagnoses(tmp_path):
    with pytest.raises(WorkshopifyError, match=r'no pyproject\.toml'):
        Config.load(tmp_path)


def test_discover_walks_up_from_a_subdirectory(fixture_repo):
    sub = fixture_repo / 'src'
    assert Config.discover(sub).root == fixture_repo.resolve()


def test_discover_ignores_a_pyproject_without_the_table(fixture_repo):
    """A pyproject.toml is not a workshop root; the [tool.workshopify] table is.

    Nested projects are ordinary -- a docs build, a vendored package -- and
    stopping the walk at the first pyproject.toml would answer with one that
    configures nothing instead of with the workshop enclosing it.
    """
    nested = fixture_repo / 'sub'
    nested.mkdir()
    (nested / 'pyproject.toml').write_text('[project]\nname = "sub"\n')
    assert Config.discover(nested).root == fixture_repo.resolve()


def test_discover_without_a_workshop_anywhere_diagnoses(tmp_path):
    with pytest.raises(WorkshopifyError, match='workshopify'):
        Config.discover(tmp_path)


def test_render_files_must_be_strings(fixture_repo):
    rewrite(fixture_repo, 'files = ["src/*.py"]', 'files = [1]')
    with pytest.raises(WorkshopifyError, match='files'):
        Config.load(fixture_repo)


def test_context_must_name_a_module(fixture_repo):
    rewrite(fixture_repo, 'context = "context.py"', 'context = 3')
    with pytest.raises(WorkshopifyError, match='context must name'):
        Config.load(fixture_repo)


def test_build_branch_must_be_a_string(fixture_repo):
    rewrite(
        fixture_repo,
        '[tool.workshopify.build]\n',
        '[tool.workshopify.build]\nbranch = 3\n',
    )
    with pytest.raises(WorkshopifyError, match='branch must be a string'):
        Config.load(fixture_repo)


def test_a_subtable_must_be_a_table(fixture_repo):
    rewrite(
        fixture_repo,
        'context = "context.py"',
        'context = "context.py"\nnotebooks = 3',
    )
    with pytest.raises(
        WorkshopifyError,
        match=r'\[tool\.workshopify\.notebooks\] must be a table',
    ):
        Config.load(fixture_repo)


def test_params_must_be_a_table(fixture_repo):
    rewrite(fixture_repo, '[tool.workshopify.params]\ngreeting = "hello"\n', '')
    rewrite(
        fixture_repo,
        'context = "context.py"',
        'context = "context.py"\nparams = 3',
    )
    with pytest.raises(
        WorkshopifyError,
        match=r'\[tool\.workshopify\.params\] must be a table',
    ):
        Config.load(fixture_repo)


def test_every_absent_key_takes_its_field_default(fixture_repo):
    """A key pyproject.toml omits falls through to the dataclass, whole table over.

    The expectation is read off the fields rather than restated as literals,
    so this covers a field the day it is added and cannot drift from the
    defaults it is checking: a workshop that configures nothing gets exactly
    what config.py declares, which is the only place a default is written.
    """
    rewrite(fixture_repo, 'files = ["src/*.py"]\n', '')
    rewrite(fixture_repo, 'include = ["Dockerfile"]\n', '')
    config = Config.load(fixture_repo)
    for section, target in (
        (config.render, RenderConfig),
        (config.build, BuildConfig),
        (config.notebooks, NotebooksConfig),
    ):
        for f in fields(target):
            assert getattr(section, f.name) == f.default, f'{target.__name__}.{f.name}'


def test_a_field_of_an_unreadable_type_says_so():
    """A field of a type nothing parses is workshopify's bug, not a config error.

    No pyproject.toml can provoke it -- only adding a field here can -- so it
    must not arrive as a KeyError from a lookup table nobody remembers, on a
    load that used to work. Naming the field and its annotation is what makes
    the gap fixable by whoever opened it.
    """

    @dataclass(frozen=True)
    class Unreadable:
        depth: int = 1

    with pytest.raises(TypeError, match=r'Unreadable\.depth'):
        config_module._sub({}, 'build', Unreadable)


def test_kebab_keys_name_snake_case_fields(fixture_repo):
    rewrite(
        fixture_repo,
        '[tool.workshopify.build]\n',
        '[tool.workshopify.build]\nkeep-tables = ["project"]\n',
    )
    assert Config.load(fixture_repo).build.keep_tables == ('project',)


def test_unknown_key_diagnoses(fixture_repo):
    rewrite(
        fixture_repo,
        '[tool.workshopify]\n',
        '[tool.workshopify]\nbranch = "workshop"\n',
    )
    with pytest.raises(
        WorkshopifyError,
        match=r"unknown key 'branch' in \[tool\.workshopify\]",
    ):
        Config.load(fixture_repo)


def test_unknown_build_key_diagnoses(fixture_repo):
    rewrite(fixture_repo, 'include = ["Dockerfile"]', 'includes = ["Dockerfile"]')
    with pytest.raises(
        WorkshopifyError,
        match=r"unknown key 'includes' in \[tool\.workshopify\.build\]",
    ):
        Config.load(fixture_repo)


def test_unknown_render_key_diagnoses(fixture_repo):
    rewrite(fixture_repo, 'files = ["src/*.py"]', 'file = ["src/*.py"]')
    with pytest.raises(
        WorkshopifyError,
        match=r"unknown key 'file' in \[tool\.workshopify\.render\]",
    ):
        Config.load(fixture_repo)


def test_unknown_notebooks_key_diagnoses(fixture_repo):
    rewrite(
        fixture_repo,
        '[tool.workshopify.params]\n',
        '[tool.workshopify.notebooks]\nsrcs = "src"\n\n[tool.workshopify.params]\n',
    )
    with pytest.raises(
        WorkshopifyError,
        match=r"unknown key 'srcs' in \[tool\.workshopify\.notebooks\]",
    ):
        Config.load(fixture_repo)


def test_params_keys_are_arbitrary(fixture_repo):
    """params is the user's own namespace: its keys are not workshopify's."""
    rewrite(
        fixture_repo,
        'greeting = "hello"',
        'greeting = "hello"\nbranch = "not a build key"',
    )
    assert Config.load(fixture_repo).params == {
        'greeting': 'hello',
        'branch': 'not a build key',
    }
