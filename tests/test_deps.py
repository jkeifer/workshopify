from __future__ import annotations

import tomllib
import zipfile

from pathlib import Path

import pytest
import tomlkit

from conftest import uv_lock

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
    """A `[header]` inside a multiline string is prose, not a table.

    Keeping only the tables `keep` names has to go through a TOML parser to
    tell the two apart; scanning lines cannot.
    """
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


def test_derive_rejects_a_keep_path_listed_with_its_own_descendant():
    """`tool.uv` and `tool.uv.sources` together have no one reading.

    Picking the narrow one is silent: `package = false` would vanish from the
    published pyproject, which is the opposite of what naming the parent asks
    for.
    """
    text = (
        '[project]\n'
        'name = "x"\n'
        '\n'
        '[tool.uv]\n'
        'package = false\n'
        '\n'
        '[tool.uv.sources]\n'
        'workshopify = { workspace = true }\n'
    )
    with pytest.raises(WorkshopifyError, match=r'tool\.uv\.sources'):
        derive_pyproject(text, ('project', 'tool.uv', 'tool.uv.sources'), ())


def test_derive_rejects_the_descendant_listed_first_too():
    """Order does not decide it: the contradiction is the same either way."""
    text = '[project]\nname = "x"\n\n[tool.uv]\npackage = false\n'
    with pytest.raises(WorkshopifyError, match=r'tool\.uv'):
        derive_pyproject(text, ('project', 'tool.uv.sources', 'tool.uv'), ())


def test_derive_rejects_a_drop_path_listed_with_its_own_descendant():
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
    with pytest.raises(WorkshopifyError, match='drop path'):
        derive_pyproject(text, KEEP, ('tool.uv', 'tool.uv.workspace'))


# --- write_deps ------------------------------------------------------------


def _wheel(index: Path, name: str, version: str) -> None:
    """A wheel holding nothing but the metadata a resolution reads.

    Hand-built so the whole test stays inside tmp_path: what `write_deps`
    promises is only observable against a package that exists in more than one
    version, and fetching one would make an offline build's test need a
    network. The fixed timestamp keeps the file byte-identical between runs.
    """
    # A wheel filename splits on '-', so the name is escaped rather than spelled.
    escaped = name.replace('-', '_')
    dist = f'{escaped}-{version}.dist-info'
    with zipfile.ZipFile(index / f'{escaped}-{version}-py3-none-any.whl', 'w') as whl:
        for entry, content in (
            (
                f'{dist}/METADATA',
                f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n',
            ),
            (
                f'{dist}/WHEEL',
                'Wheel-Version: 1.0\nGenerator: test\n'
                'Root-Is-Purelib: true\nTag: py3-none-any\n',
            ),
            (f'{dist}/RECORD', ''),
        ):
            whl.writestr(
                zipfile.ZipInfo(entry, date_time=(2020, 1, 1, 0, 0, 0)), content
            )


def _depend_on(repo: Path, name: str, index: Path) -> None:
    """Give the fixture repo one dependency, resolvable only from `index`.

    Written through [tool.uv], which `keep-tables` ships, so the derived
    pyproject can be re-resolved against the same index that the repo's own
    lock came from.
    """
    doc = tomlkit.parse((repo / 'pyproject.toml').read_text())
    doc['project']['dependencies'] = [name]
    doc['tool']['uv']['no-index'] = True
    doc['tool']['uv']['find-links'] = [str(index)]
    (repo / 'pyproject.toml').write_text(tomlkit.dumps(doc))


def _pins(lock: Path) -> dict[str, str]:
    return {p['name']: p['version'] for p in tomllib.loads(lock.read_text())['package']}


def test_write_deps_derives_the_pyproject_and_reports_both_files(
    fixture_repo,
    tmp_path,
):
    uv_lock(fixture_repo, '--offline')
    staging = tmp_path / 'staging'
    staging.mkdir()
    written = deps.write_deps(Config.load(fixture_repo), staging)
    # The build subtracts what its steps wrote from the branch's tracked
    # files, so a step that under-reports gets its own output called cruft.
    assert written == [staging / 'pyproject.toml', staging / 'uv.lock']
    assert all(path.is_file() for path in written)
    data = tomllib.loads((staging / 'pyproject.toml').read_text())
    assert data['project']['name'] == 'fixture-workshop'
    assert set(data.get('tool', {})) == {'uv'}
    assert 'dependency-groups' not in data


def test_write_deps_pins_what_the_repo_pins(fixture_repo, tmp_path):
    """The seeding is the whole point, so it has to be observable.

    The fixture depends on a package that exists twice over in a local flat
    index, and the repo locks while only 1.0.0 is there. A build after 2.0.0
    appears must still ship 1.0.0: without the seed the re-resolution is an
    independent one, and takes the newest thing it can see.
    """
    index = tmp_path / 'index'
    index.mkdir()
    _wheel(index, 'workshopify-fixture-dep', '1.0.0')
    _depend_on(fixture_repo, 'workshopify-fixture-dep', index)
    uv_lock(fixture_repo, '--offline')
    assert _pins(fixture_repo / 'uv.lock')['workshopify-fixture-dep'] == '1.0.0'

    _wheel(index, 'workshopify-fixture-dep', '2.0.0')
    staging = tmp_path / 'staging'
    staging.mkdir()
    deps.write_deps(Config.load(fixture_repo), staging)
    assert _pins(staging / 'uv.lock')['workshopify-fixture-dep'] == '1.0.0'


def test_write_deps_without_a_repo_lock_names_the_command_that_makes_one(
    fixture_repo,
    tmp_path,
):
    """A repo that never ran `uv lock` is an authoring state, not a traceback."""
    staging = tmp_path / 'staging'
    staging.mkdir()
    with pytest.raises(WorkshopifyError, match='run `uv lock`'):
        deps.write_deps(Config.load(fixture_repo), staging)


def test_write_deps_carries_what_uv_said_when_it_refused(fixture_repo, tmp_path):
    """uv's stderr is the diagnostic; the wrapper only says where it happened."""
    uv_lock(fixture_repo, '--offline')
    index = tmp_path / 'empty-index'
    index.mkdir()
    # Locked with no dependencies, then asked for one nothing offline can supply.
    _depend_on(fixture_repo, 'workshopify-fixture-dep', index)
    staging = tmp_path / 'staging'
    staging.mkdir()
    with pytest.raises(WorkshopifyError) as excinfo:
        deps.write_deps(Config.load(fixture_repo), staging)
    assert 'uv lock --offline failed' in str(excinfo.value)
    assert 'workshopify-fixture-dep' in str(excinfo.value)


def test_write_deps_diagnoses_a_uv_it_cannot_run(fixture_repo, tmp_path, monkeypatch):
    """Nothing makes a pipx-installed workshopify have uv on PATH."""
    uv_lock(fixture_repo, '--offline')
    config = Config.load(fixture_repo)
    staging = tmp_path / 'staging'
    staging.mkdir()
    monkeypatch.setenv('PATH', str(tmp_path / 'empty'))
    with pytest.raises(WorkshopifyError, match='uv lock --offline'):
        deps.write_deps(config, staging)
