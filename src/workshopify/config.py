"""The declarative half of a workshop repo: [tool.workshopify] in pyproject.

Everything mechanical the tool does is driven from here; the one procedural
piece -- the context module -- is only *named* here and executed by
workshopify.context.

The frozen dataclasses below hold every default there is. `load` passes only
the keys pyproject.toml actually gave, so an absent key falls through to its
field default rather than to a literal restated at the call site.
"""

from __future__ import annotations

import tomllib

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Self

from workshopify.errors import WorkshopifyError

type _Table = dict[str, Any]


def _where(*path: str) -> str:
    """A config table, named as pyproject.toml spells it."""
    return f'[{".".join(("tool", "workshopify", *path))}]'


def _toml_key(name: str) -> str:
    """A dataclass field, named as pyproject.toml spells it.

    TOML keys are kebab-case and dataclass fields are snake_case; this is the
    only place the two spellings meet.
    """
    return name.replace('_', '-')


def _checked(table: _Table, known: set[str], where: str) -> _Table:
    """`table` keyed by field name, rejecting any key nothing declares.

    An unrecognized key is a typo, and a typo'd key configures nothing: a
    workshop that asked for `includes = [...]` would build without the file it
    named and say nothing about it. So say it here, while the name the author
    wrote is still in hand.
    """
    names = {_toml_key(name): name for name in known}
    checked = {}
    for key, value in table.items():
        if key not in names:
            raise WorkshopifyError(f'error: unknown key {key!r} in {where}')
        checked[names[key]] = value
    return checked


def _given(**values: Any) -> _Table:
    """The keywords a value was actually given for.

    What pyproject.toml leaves out is left out of the constructor call, so the
    dataclass default -- the only default -- applies.
    """
    return {name: value for name, value in values.items() if value is not None}


def _section(parent: _Table, name: str, target: type[Any]) -> tuple[_Table, str]:
    """A subtable checked against `target`, with its name for the reads to come."""
    where = _where(name)
    table = parent.get(name, {})
    if not isinstance(table, dict):
        raise WorkshopifyError(f'error: {where} must be a table')
    return _checked(table, {f.name for f in fields(target)}, where), where


def _string(table: _Table, key: str, where: str) -> str | None:
    value = table.get(key)
    if value is not None and not isinstance(value, str):
        raise WorkshopifyError(f'error: {where} {_toml_key(key)} must be a string')
    return value


def _strings(table: _Table, key: str, where: str) -> tuple[str, ...] | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(i, str) for i in value):
        raise WorkshopifyError(
            f'error: {where} {_toml_key(key)} must be a list of strings',
        )
    return tuple(value)


def _workshopify(pyproject: Path) -> _Table | None:
    """The [tool.workshopify] table of a pyproject, or None if it has none."""
    with pyproject.open('rb') as f:
        return tomllib.load(f).get('tool', {}).get('workshopify')


@dataclass(frozen=True)
class RenderConfig:
    files: tuple[str, ...] = ()


@dataclass(frozen=True)
class BuildConfig:
    branch: str = 'workshop'
    include: tuple[str, ...] = ()
    keep_tables: tuple[str, ...] = ('project', 'tool.uv')
    drop_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class NotebooksConfig:
    src: str = 'src'


@dataclass(frozen=True)
class Config:
    root: Path
    context: Path
    render: RenderConfig
    build: BuildConfig
    notebooks: NotebooksConfig
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def pyproject(self) -> Path:
        return self.root / 'pyproject.toml'

    def display_path(self, path: Path) -> str:
        """`path` spelled as a reader of this repo would: relative to the root.

        A path outside the root has no relative spelling that means anything,
        so it falls back to its own full form. Lives here because `root` does:
        every caller already holds the Config, and both the strings `check()`
        builds and the lines the CLI prints must name a file the same way.
        """
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def rebase(self, path: Path, dest: Path, what: str) -> Path:
        """`path`, re-rooted under `dest`. Outside the root is a config error.

        Every generated tree is the repo's own layout written somewhere else,
        so this is the one operation that says where a repo path lands there.
        Lives beside `display_path` because both are the root answering a
        question about a path, and the root is what this object owns.

        Joining an absolute path discards `dest` entirely, so the subtraction
        against the root comes first -- without it a build writes over the
        source tree and reports success. Both sides are resolved because one
        directory has more than one spelling: on macOS a path under /var and
        the same path under /private/var name the same place. A path outside
        the root has no spelling under `dest` at all, which is a config error
        worth naming rather than `relative_to`'s bare ValueError.
        """
        try:
            return dest / path.resolve().relative_to(self.root.resolve())
        except ValueError as e:
            raise WorkshopifyError(
                f'error: {what} {path} is outside the repo root {self.root}',
            ) from e

    @classmethod
    def _of(cls, root: Path, table: _Table) -> Self:
        """A config from an already-parsed table and the resolved root it came from."""
        # `root` is where the config was found, not something it declares.
        known = {f.name for f in fields(cls)} - {'root'}
        top = _checked(table, known, _where())

        context = top.get('context')
        if not isinstance(context, str):
            raise WorkshopifyError(
                f'error: {_where()} context must name the context module',
            )

        # params is the workshop author's own namespace -- its keys are
        # whatever the context module reads -- so only its shape is checked.
        params = top.get('params', {})
        if not isinstance(params, dict):
            raise WorkshopifyError(f'error: {_where("params")} must be a table')

        render, at_render = _section(top, 'render', RenderConfig)
        build, at_build = _section(top, 'build', BuildConfig)
        notebooks, at_notebooks = _section(top, 'notebooks', NotebooksConfig)
        return cls(
            root=root,
            context=Path(context),
            render=RenderConfig(**_given(files=_strings(render, 'files', at_render))),
            build=BuildConfig(
                **_given(
                    branch=_string(build, 'branch', at_build),
                    include=_strings(build, 'include', at_build),
                    keep_tables=_strings(build, 'keep_tables', at_build),
                    drop_keys=_strings(build, 'drop_keys', at_build),
                ),
            ),
            notebooks=NotebooksConfig(
                **_given(src=_string(notebooks, 'src', at_notebooks)),
            ),
            params=dict(params),
        )

    @classmethod
    def load(cls, root: Path) -> Self:
        root = root.resolve()
        pyproject = root / 'pyproject.toml'
        if not pyproject.is_file():
            raise WorkshopifyError(f'error: no pyproject.toml in {root}')
        table = _workshopify(pyproject)
        if table is None:
            raise WorkshopifyError(
                f'error: no {_where()} table in {pyproject}',
            )
        return cls._of(root, table)

    @classmethod
    def discover(cls, start: Path | None = None) -> Self:
        """The nearest enclosing repo with a [tool.workshopify] table."""
        start = (start or Path.cwd()).resolve()
        for candidate in (start, *start.parents):
            pyproject = candidate / 'pyproject.toml'
            if pyproject.is_file():
                table = _workshopify(pyproject)
                if table is not None:
                    return cls._of(candidate, table)
        raise WorkshopifyError(
            f'error: no pyproject.toml with {_where()} at or above {start}',
        )
