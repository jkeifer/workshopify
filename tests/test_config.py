from dataclasses import dataclass
from pathlib import Path

import pytest

from workshopify import config as config_module
from workshopify.config import BuildConfig, Config, NotebooksConfig
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


def test_discover_ignores_a_pyproject_without_the_table(fixture_repo, tmp_path):
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


def test_build_defaults_come_from_the_dataclass(fixture_repo, monkeypatch):
    """A key absent from pyproject.toml takes the dataclass default.

    The dataclasses are the only place a default is written down, so changing
    one here changes what an unconfigured workshop gets.
    """

    @dataclass(frozen=True)
    class OtherDefaults(BuildConfig):
        branch: str = 'published'
        keep_tables: tuple[str, ...] = ('project',)

    monkeypatch.setattr(config_module, 'BuildConfig', OtherDefaults)
    config = Config.load(fixture_repo)
    assert config.build.branch == 'published'
    assert config.build.keep_tables == ('project',)


def test_notebooks_default_comes_from_the_dataclass(fixture_repo, monkeypatch):
    @dataclass(frozen=True)
    class OtherDefaults(NotebooksConfig):
        src: str = 'sources'

    monkeypatch.setattr(config_module, 'NotebooksConfig', OtherDefaults)
    assert Config.load(fixture_repo).notebooks.src == 'sources'


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
