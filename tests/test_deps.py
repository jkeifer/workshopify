from __future__ import annotations

import subprocess
import tomllib

import pytest

from workshopify import deps
from workshopify.config import Config
from workshopify.deps import derive_pyproject
from workshopify.errors import WorkshopifyError

KEEP = ('project', 'tool.uv')


# --- derive_pyproject ------------------------------------------------------


def test_derive_keeps_only_the_kept_tables(fixture_repo):
    derived = derive_pyproject(
        (fixture_repo / 'pyproject.toml').read_text(),
        KEEP,
        (),
    )
    data = tomllib.loads(derived)
    assert data['project']['name'] == 'fixture-workshop'
    assert data['tool'] == {'uv': {'package': False}}
    assert 'dependency-groups' not in data


def test_derive_preserves_comments_and_formatting():
    text = (
        '[project]\n'
        'name = "x"  # the name\n'
        '# a comment inside the kept table\n'
        'version = "1.0"\n'
        '\n'
        '[tool.ruff]\n'
        'line-length = 100\n'
        '\n'
        '[tool.uv]\n'
        'package = false\n'
    )
    derived = derive_pyproject(text, KEEP, ())
    assert '# the name' in derived
    assert '# a comment inside the kept table' in derived
    assert 'ruff' not in derived


def test_derive_survives_a_multiline_string_containing_a_header():
    """The hazard that killed the line-based parser: [tool.ruff] as prose."""
    text = (
        '[project]\n'
        'name = "x"\n'
        "description = '''\n"
        '[tool.ruff] is prose here, not a header\n'
        "'''\n"
    )
    data = tomllib.loads(derive_pyproject(text, ('project',), ()))
    assert '[tool.ruff]' in data['project']['description']


def test_derive_keeps_a_nested_path():
    """A keep path is any depth, and keeps only that child of its parent."""
    text = (
        '[project]\n'
        'name = "x"\n'
        '\n'
        '[tool.ruff]\n'
        'line-length = 100\n'
        '\n'
        '[tool.uv]\n'
        'package = false\n'
        '\n'
        '[tool.uv.sources]\n'
        'workshopify = { workspace = true }\n'
    )
    data = tomllib.loads(derive_pyproject(text, ('project', 'tool.uv.sources'), ()))
    assert data['tool'] == {'uv': {'sources': {'workshopify': {'workspace': True}}}}


def test_derive_keeps_the_whole_subtree_a_path_names():
    """A kept path keeps everything under it; drop then carves pieces out."""
    text = (
        '[project]\n'
        'name = "x"  # the name\n'
        '\n'
        '[tool.ruff]\n'
        'line-length = 100\n'
        '\n'
        '[tool.uv]\n'
        '# a comment inside the kept table\n'
        'package = false\n'
        '\n'
        '[tool.uv.sources]\n'
        'workshopify = { workspace = true }\n'
        '\n'
        '[tool.uv.workspace]\n'
        'members = ["workshopify"]\n'
    )
    derived = derive_pyproject(text, KEEP, ('tool.uv.workspace',))
    assert '# the name' in derived
    assert '# a comment inside the kept table' in derived
    data = tomllib.loads(derived)
    assert data['tool'] == {
        'uv': {'package': False, 'sources': {'workshopify': {'workspace': True}}},
    }


def test_derive_rejects_a_keep_path_matching_nothing():
    text = '[project]\nname = "x"\n\n[tool.uv]\npackage = false\n'
    with pytest.raises(WorkshopifyError, match='projekt'):
        derive_pyproject(text, ('projekt', 'tool.uv'), ())


def test_derive_rejects_a_deep_keep_path_matching_nothing():
    text = '[project]\nname = "x"\n\n[tool.uv]\npackage = false\n'
    with pytest.raises(WorkshopifyError, match=r'tool\.uv\.sources'):
        derive_pyproject(text, ('project', 'tool.uv.sources'), ())


def test_derive_drops_the_configured_keys():
    text = (
        '[project]\n'
        'name = "x"\n'
        '\n'
        '[tool.uv]\n'
        'package = false\n'
        '\n'
        '[tool.uv.workspace]\n'
        'members = ["workshopify"]\n'
        '\n'
        '[tool.uv.sources]\n'
        'workshopify = { workspace = true }\n'
    )
    derived = derive_pyproject(
        text,
        KEEP,
        ('tool.uv.workspace', 'tool.uv.sources'),
    )
    data = tomllib.loads(derived)
    assert data['tool']['uv'] == {'package': False}


def test_derive_rejects_a_drop_path_matching_nothing():
    """A typo here would ship the workspace wiring; it must not be silent."""
    text = (
        '[project]\n'
        'name = "x"\n'
        '\n'
        '[tool.uv]\n'
        'package = false\n'
        '\n'
        '[tool.uv.workspace]\n'
        'members = ["workshopify"]\n'
    )
    with pytest.raises(WorkshopifyError, match=r'tool\.uv\.wrkspace'):
        derive_pyproject(text, KEEP, ('tool.uv.wrkspace',))


# --- write_deps ------------------------------------------------------------


def test_write_deps_produces_a_lock(fixture_repo, tmp_path):
    subprocess.run(['uv', 'lock'], cwd=fixture_repo, check=True, capture_output=True)
    config = Config.load(fixture_repo)
    staging = tmp_path / 'staging'
    staging.mkdir()
    deps.write_deps(config, staging)
    assert (staging / 'uv.lock').is_file()
    data = tomllib.loads((staging / 'pyproject.toml').read_text())
    assert data['project']['name'] == 'fixture-workshop'
    assert set(data.get('tool', {})) == {'uv'}
    assert 'dependency-groups' not in data
