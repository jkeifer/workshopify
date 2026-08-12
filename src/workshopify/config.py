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

from collections.abc import Callable
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self, get_type_hints

from workshopify import fs
from workshopify.errors import WorkshopifyError, diagnosed

if TYPE_CHECKING:
    # The only spelling of "a dataclass, as a type" that a checker accepts;
    # it is what lets `_sub` hand back the class it was given rather than Any,
    # so a subtable wired to the wrong dataclass is a type error here.
    from _typeshed import DataclassInstance

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


# How a subtable field is read, keyed by the annotation the dataclass gives it.
# This is the vocabulary a subtable may be written in; a field annotated with
# anything else has no reading, and `_sub` says so rather than guessing.
_PARSERS: dict[Any, Callable[[_Table, str, str], Any]] = {
    str: _string,
    tuple[str, ...]: _strings,
}


def _sub[T: DataclassInstance](parent: _Table, name: str, target: type[T]) -> T:
    """A subtable parsed into `target`, one field per field it declares.

    The dataclass is the whole spelling of the subtable: its fields name the
    keys, its annotations say how each is read, and its defaults are what an
    absent key falls through to. Adding a config key is therefore one line
    there and nothing here -- which is the point, because the form this
    replaced named every key three times over and a key that reached the
    constructor without a validator was accepted whatever it held.

    An annotation `_PARSERS` has no entry for is a gap in that vocabulary, not
    a bad pyproject.toml: no author can cause it and none can fix it, so it
    raises TypeError naming the field and the type, and arrives with the
    traceback a bug in this package wants, rather than as a bare KeyError.
    """
    where = _where(name)
    table = parent.get(name, {})
    if not isinstance(table, dict):
        raise WorkshopifyError(f'error: {where} must be a table')
    table = _checked(table, {f.name for f in fields(target)}, where)
    hints = get_type_hints(target)
    values = {}
    for f in fields(target):
        parser = _PARSERS.get(hints[f.name])
        if parser is None:
            raise TypeError(
                f'no config parser for {target.__name__}.{f.name}: {hints[f.name]}',
            )
        values[f.name] = parser(table, f.name, where)
    return target(**_given(**values))


def _workshopify(pyproject: Path) -> _Table | None:
    """The [tool.workshopify] table of a pyproject, or None if it has none.

    A malformed pyproject.toml is the likeliest mistake a workshop author will
    make, in the one file this tool is configured from, so its decode error is
    handed to `diagnosed` explicitly: TOMLDecodeError is a ValueError, which
    `diagnosed` does not catch by default and must not be widened to.
    """
    text = fs.read(pyproject)
    with diagnosed(f'parsing {pyproject}', tomllib.TOMLDecodeError):
        table = tomllib.loads(text)
    return table.get('tool', {}).get('workshopify')


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

    def within(self, path: Path, what: str) -> Path:
        """`path` as this repo spells it: relative to the root, or a refusal.

        The judgment, separate from any use of it, because two callers want
        different things from the same verdict: `rebase` wants somewhere to
        put the answer, while `context` wants only to know that a declared
        asset target is a place this repo has -- and asking a mapping function
        for its judgment and dropping what it mapped is how that was said
        before there was a name for it.

        Both sides are resolved because one directory has more than one
        spelling: on macOS a path under /var and the same path under
        /private/var name the same place, and comparing them unresolved
        refuses a path that is genuinely inside. A path that really is outside
        has no spelling relative to the root at all, which is a config error
        worth naming -- `what` is how the caller names the kind of path it
        handed over -- rather than `relative_to`'s bare ValueError.
        """
        try:
            return path.resolve().relative_to(self.root.resolve())
        except ValueError as e:
            raise WorkshopifyError(
                f'error: {what} {path} is outside the repo root {self.root}',
            ) from e

    def rebase(self, path: Path, dest: Path, what: str) -> Path:
        """`path`, re-rooted under `dest`. Outside the root is a config error.

        Every generated tree is the repo's own layout written somewhere else,
        so this is the one operation that says where a repo path lands there.
        Lives beside `display_path` because both are the root answering a
        question about a path, and the root is what this object owns.

        What is joined is `within`'s answer and never the path itself:
        joining an absolute path discards `dest` entirely, so without that
        subtraction a build writes over the source tree and reports success.
        """
        return dest / self.within(path, what)

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

        # Only the three uniform subtables go through `_sub`; `context` and
        # `params` above are each one of a kind -- one mandatory and read as a
        # path, one an arbitrary namespace only shape-checked -- and covering
        # them would cost an annotation-to-behavior vocabulary richer than the
        # code it replaced.
        return cls(
            root=root,
            context=Path(context),
            render=_sub(top, 'render', RenderConfig),
            build=_sub(top, 'build', BuildConfig),
            notebooks=_sub(top, 'notebooks', NotebooksConfig),
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
