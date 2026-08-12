"""Render cog-templated sources, entirely in memory.

The files named by [tool.workshopify.render] carry cog generators in
comments; the values those generators produce sit inline in the file, so the
sources read normally and re-rendering them is a no-op until the underlying
data changes. This module knows nothing about what the generators compute:
their namespace comes from the context module (via workshopify.context), so
the same machinery serves any repo that templates its sources this way.

    [tool.workshopify.render]
    files = ["src/*.py"]

Three operations, each taking the loaded Context -- never a raw namespace --
so no caller has to reach inside it:

    apply(config, ctx)          re-render every configured file and asset in
                                place
    check(config, ctx)          error strings if any file or asset is stale
    publish(ctx, text, fname)   the text with markers and generators stripped,
                                validated as a sound py:percent document

apply and check work from the Config, over every file it names; publish works
over the text of one source file, so it takes that text and the name to
report it by instead.

The asset half of apply and check is workshopify.assets': a file copied
verbatim has nothing to do with cog, and all this module does with one is
sequence it against the rendering.

Failures raise WorkshopifyError/PublishError; deciding to exit is the CLI's
job, never this module's.
"""

from __future__ import annotations

import copy
import difflib
import io

from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import jupytext

from cogapp import Cog
from cogapp.cogapp import CogError

from workshopify import assets, fs
from workshopify.config import Config
from workshopify.context import Context
from workshopify.errors import WorkshopifyError, diagnosed

# HTML-comment forms of cog's markers, so a marker that ever leaked into
# rendered markdown would at least be invisible. One definition, used by
# every operation and stated in the docs -- do not vary per file.
MARKERS = ('<!--[[[cog', ']]]-->', '<!--[[[end]]]-->')

# `md()` emits generator output as markdown-cell comment lines. It is defined
# here, not in the context module, because it needs the `cog` object that
# cogapp injects into the generator namespace at run time.
PROLOGUE = "def md(o): cog.outl('\\n'.join('# ' + l for l in str(o).split('\\n')))"


class PublishError(WorkshopifyError):
    """Stripped output that is structurally unsound and must not ship."""


def _isolated(namespace: dict[str, Any]) -> dict[str, Any]:
    """The namespace copied deeply enough that a generator cannot outlive itself.

    cog execs the generators straight against the mapping it is handed, so a
    generator that appends to a list it found there changes what the next
    file, the next run, and `check` after `apply` all see -- making the output
    a function of how many times it has been rendered, which is exactly what
    "generated output is a pure function of its source" forbids. A shallow
    copy protects the binding and not the object bound, so it protects
    nothing that a generator would plausibly reach for.

    Deep enough, not exhaustive. A value that refuses to be copied -- a module
    the context imported, an open handle -- is passed through as it is,
    because refusing to render over one would be a worse failure than the
    mutation this guards against; and a function defined in the context module
    still closes over that module's real globals no matter what is done here.
    """
    isolated = {}
    for name, value in namespace.items():
        try:
            isolated[name] = copy.deepcopy(value)
        except Exception:  # noqa: BLE001 -- see above: uncopyable is not fatal
            isolated[name] = value
    return isolated


def _run(text: str, namespace: dict[str, Any], fname: str, *, delete_code: bool) -> str:
    cog = Cog()
    options = cog.options
    options.begin_spec, options.end_spec, options.end_output = MARKERS
    options.prologue = PROLOGUE
    options.delete_code = delete_code
    # Everything cog says goes into a buffer nobody reads. It is a command-line
    # tool underneath: `cog.msg()` and `cog.error()` land on the process
    # stdout, and a `print()` in a generator -- the first thing anyone reaches
    # for while debugging one -- lands there too. That stream is where
    # `workshopify worktree` writes its one machine-readable line, so chatter
    # on it is not noise but corruption. Both halves are needed: cog binds its
    # own `stdout` at construction, while a generator's `print` resolves
    # `sys.stdout` when it runs.
    chatter = io.StringIO()
    cog.set_output(stdout=chatter, stderr=chatter)
    out = io.StringIO()
    # cog's own errors are what an authoring mistake looks like -- an unclosed
    # marker, or a generator that throws, which arrives as CogUserException
    # (a CogError) carrying the generator's traceback. Diagnosed here because
    # every command runs this, so untranslated they would reach cli.main as a
    # traceback for the single most likely thing a workshop author gets wrong.
    with diagnosed(fname, CogError), redirect_stdout(chatter):
        cog.process_file(
            io.StringIO(text),
            out,
            fname=fname,
            globals=_isolated(namespace),
        )
    return out.getvalue()


def _files(config: Config) -> list[Path]:
    files = sorted(
        path
        for pattern in config.render.files
        for path in fs.glob(config.root, pattern)
    )
    if not files:
        raise WorkshopifyError(
            f'error: [tool.workshopify.render] files matched nothing: '
            f'{list(config.render.files)}',
        )
    return files


def _rendered(config: Config, ctx: Context) -> dict[Path, str]:
    """Every configured file re-rendered, in memory, none written."""
    return {
        path: _run(fs.read(path), ctx.namespace, str(path), delete_code=False)
        for path in _files(config)
    }


def apply(config: Config, ctx: Context) -> list[Path]:
    """Re-render every configured file and asset in place; what changed.

    No write here is atomic, and the guarantee is narrower than that: what is
    all-or-nothing is the *deciding*. Every file is rendered in memory and
    every asset source verified to exist before anything is written, so the
    two failures that actually happen -- a typo'd generator, a screenshot
    renamed out from under the context module -- are caught while the tree is
    still untouched. Both halves therefore name what they will do before
    either does any of it -- `writes` and `copies` are the whole of the
    decision, and what is returned is those two rather than a tally kept
    alongside the doing. The writes and copies themselves run in sequence, so
    a permissions error or a full disk partway through leaves the earlier ones
    applied. Buying atomicity there would mean staging every rendered file and
    every asset beside its target to guard against failures this rare.

    Recoverable rather than atomic, then, and that is enough because apply is
    idempotent: it writes only what differs from what it would write, so a
    second run once the cause is cleared applies just the remainder and
    converges. A run with nothing to do writes nothing and returns [].
    """
    writes = {
        path: text
        for path, text in _rendered(config, ctx).items()
        if fs.read(path) != text
    }
    copies = assets.stale(ctx)
    for path, text in writes.items():
        fs.write(path, text)
    return [*writes, *assets.apply(copies)]


def check(config: Config, ctx: Context) -> list[str]:
    """Error strings for every stale rendered file and every wrong asset."""
    errors = []
    for path, fresh in _rendered(config, ctx).items():
        current = fs.read(path)
        if current == fresh:
            continue
        name = config.display_path(path)
        diff = difflib.unified_diff(
            current.splitlines(),
            fresh.splitlines(),
            f'{name} (current)',
            f'{name} (regenerated)',
            lineterm='',
            n=0,
        )
        errors.append('\n'.join([f'{name}: stale rendered values', *diff]))
    return errors + assets.errors(config, ctx)


def publish(ctx: Context, text: str, fname: str = '<src>') -> str:
    """Marker- and generator-free text, validated before it can ship.

    cog only checks that generators ran; it does not know notebook cells
    exist. The failure that matters here is structural -- output landing
    outside its cell, or half a markdown line losing its comment prefix --
    so the stripped text must parse to the same cell shape as its source
    and survive a jupytext round trip byte-identically.
    """
    stripped = _run(text, ctx.namespace, fname, delete_code=True)
    if MARKERS[0] in stripped or MARKERS[2] in stripped:
        raise PublishError(f'error: {fname}: marker residue survived the strip')
    before = jupytext.reads(text, fmt='py:percent')
    after = jupytext.reads(stripped, fmt='py:percent')
    shape = [(c.cell_type, bool(c.source.strip())) for c in before.cells]
    if shape != [(c.cell_type, bool(c.source.strip())) for c in after.cells]:
        raise PublishError(
            f'error: {fname}: cell structure changed during strip '
            f'({len(before.cells)} cells -> {len(after.cells)})',
        )
    if jupytext.writes(after, fmt='py:percent') != stripped:
        raise PublishError(
            f'error: {fname}: stripped text is not a jupytext fixed point -- '
            'a generator likely emitted a line without its comment prefix',
        )
    return stripped
