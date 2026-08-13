"""The one command-line surface, and the one place a failure becomes an exit.

Every module below raises WorkshopifyError carrying the full diagnostic;
main() prints it to stderr and returns 1. Nothing else in the package may
exit the process, and nothing else may print: the modules below return what
they computed, and every line the user sees is rendered here.

click parses and does no more than that. `standalone_mode=False` is what
holds it to that: it hands main() the usage error instead of showing it and
exiting, so a misspelled option reaches the user by the same route a broken
config does, and the exit stays this module's to decide.
"""

from __future__ import annotations

import io
import sys

from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Concatenate

import click

from workshopify import build, context, notebooks, params, render, worktree
from workshopify.config import Config
from workshopify.errors import WorkshopifyError


def _err(*lines: str) -> None:
    """Everything the user is told that is not the command's result.

    Progress, advisory notes and diagnostics alike go to stderr, so the one
    thing on stdout stays a value a caller can pipe. One helper rather than
    `file=sys.stderr` on every call, so the keyword never crowds out the line
    it is attached to.
    """
    for line in lines:
        print(line, file=sys.stderr)


def _discovered[**P, R](
    command: Callable[Concatenate[Config, P], R],
) -> Callable[P, R]:
    """Hand a command the repo it works from, found when the command runs.

    Every subcommand works from the discovered repo, so discovery is spelled
    once -- here rather than in the group callback, which click runs before it
    parses the subcommand's own arguments, `--help` among them. Asking what
    `check` does would then need a workshop repo to answer, and the person
    asking is likeliest to be standing outside one. Wrapping each callback
    instead defers discovery to the invocation that has something to discover
    it for.
    """

    @wraps(command)
    def invoke(*args: P.args, **kwargs: P.kwargs) -> R:
        return command(Config.discover(), *args, **kwargs)

    return invoke


@click.group(
    help=(
        'Build a template-rendered, notebook-based workshop repository. '
        'The repo declares everything mechanical in [tool.workshopify] '
        'tables in its pyproject.toml; these commands do the rest.'
    ),
    # click offers only `--help`; argparse gave both, and `-h` is the spelling
    # a hand reaches for. Set on the group, inherited by every subcommand
    # through the context, so there is one list rather than seven.
    context_settings={'help_option_names': ['-h', '--help']},
)
def cli() -> None:
    """The group decides nothing, so that `--help` needs nothing decided.

    click runs this before it parses the subcommand's own arguments -- which
    is where `--help` is -- so anything decided here is decided before anyone
    can have asked merely what a command does. It knows which subcommand by
    now; it does not yet know that the subcommand was only asked to explain
    itself.
    """


@cli.command('render', help='re-render templated values and assets in place')
@_discovered
def _render(config: Config) -> int:
    changed = render.apply(config, context.load(config))
    _err(*(f'rendered {config.display_path(path)}' for path in changed))
    return 0


@cli.command('check', help='report stale rendered values or assets; exit 1 if any')
@click.option(
    '--skip-assets',
    is_flag=True,
    # Answerable from the environment as well as from argv, because the runner
    # that most needs this owns the argv: a pre-commit-style hook passes
    # nothing through but the environment. What it needs to say is that this
    # checkout never committed the assets, so the asset half fails for a reason
    # that says nothing about whether the repo is stale. click reads the
    # variable with a flag's vocabulary, so `=0` means do not skip and an
    # unrecognised word is a usage error naming the variable -- neither of
    # which a truthiness test on the string would get right.
    envvar='WORKSHOPIFY_SKIP_ASSETS',
    show_envvar=True,
    help='skip checking assets, for a checkout that does not carry them',
)
@_discovered
def _check(config: Config, skip_assets: bool) -> int:
    errors = render.check(
        config,
        context.load(config),
        assets_too=not skip_assets,
    )
    _err(*errors)
    if errors:
        return 1
    # Success is the one line that has to be narrower when the check was: a
    # report that claims the assets are current is worth less than nothing
    # from a run that never looked at them.
    _err(
        'rendered values are current'
        if skip_assets
        else 'rendered values and assets are current',
    )
    return 0


@cli.command('generate', help='generate the notebooks and notes from src/')
@click.option(
    # `Path`, not `click.Path`: the latter stats what it is given -- it is
    # `readable=True` by default -- and would refuse at parse time a directory
    # that the step below is the one entitled to complain about. Conversion is
    # all that is wanted here, and the metavar is `PATH` either way.
    '--output-dir',
    type=Path,
    default=None,
    help='write the generated tree here instead of into the repo',
)
@_discovered
def _generate(config: Config, output_dir: Path | None) -> int:
    written = notebooks.generate(
        config,
        context.load(config),
        output_dir or config.root,
    )
    _err(*(f'wrote {config.display_path(path)}' for path in written))
    return 0


@cli.command(
    'build',
    help='assemble the published workshop tree into its branch worktree',
)
@click.option(
    '--clean',
    is_flag=True,
    help='remove every tracked file from the worktree before building',
)
@click.option(
    '--overwrite-dirty',
    is_flag=True,
    help='build over uncommitted changes in the worktree',
)
@_discovered
def _build(config: Config, clean: bool, overwrite_dirty: bool) -> int:
    prepared = worktree.prepare_worktree(
        config.root,
        config.build.branch,
        config.root / config.build.branch,
        orphan=False,
    )
    _err(*prepared.notes)
    published = build.publish_into(
        config,
        context.load(config),
        prepared.path,
        clean_first=clean,
        overwrite_dirty=overwrite_dirty,
    )
    _publish_instructions(prepared.path)
    # Last, after the success output above, so the one failure this report
    # exists to catch -- a renamed exercise shipping silently -- does not
    # scroll off above the happy path.
    _stale_report(published.stale)
    return 0


def _publish_instructions(target: Path) -> None:
    """How to review and land the build. It commits nothing itself."""
    _err(
        f'built into {target}',
        f'review with: git -C {target} status',
        'publish with:',
        f'  git -C {target} add -A',
        f'  PREK_ALLOW_NO_CONFIG=1 git -C {target} commit -m "Update notebooks"',
        f'  git -C {target} push',
        'PREK_ALLOW_NO_CONFIG=1 is required: git hooks are shared across all '
        'worktrees,\nand the workshop branch intentionally carries no '
        'pre-commit config.',
    )


def _stale_report(stale: list[Path]) -> None:
    """Tracked files the build did not write, and how to be rid of them."""
    if not stale:
        return
    _err(
        'not written by this build:',
        *(f'  {path}' for path in stale),
        're-run with --clean to remove',
    )


@cli.command('set', help='set a [tool.workshopify.params] value and re-render')
@click.argument('key')
@click.argument('value')
@_discovered
def _set(config: Config, key: str, value: str) -> int:
    rewritten = params.set_param(config, key, value)
    _err(
        *(f'updated {config.display_path(path)}' for path in rewritten.paths),
        # The recorded value, not the argument: they differ in type whenever
        # the param is not a string, and the file now holds the former.
        f'set {key} = {rewritten.value!r}',
    )
    return 0


@cli.command('worktree', help='prepare a branch worktree without building')
@click.argument('branch')
@click.option(
    '--path',
    type=Path,
    default=None,
    help='check the worktree out here instead of at <repo>/<branch>',
)
@click.option('--orphan', is_flag=True, help='start the branch with no history')
@_discovered
def _worktree(config: Config, branch: str, path: Path | None, orphan: bool) -> int:
    prepared = worktree.prepare_worktree(
        config.root,
        branch,
        path or config.root / branch,
        orphan=orphan,
    )
    _err(*prepared.notes)
    # The path is the command's result, so it goes to stdout where it can be
    # piped; the notes above are commentary and stay on stderr.
    print(prepared.path)
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        # Discovery happens under here, one `_discovered` command in, because
        # failing to find a workshop repo is the first diagnostic a user
        # outside one gets. click's own refusals arrive by the same door: told
        # not to stand alone, it raises them here rather than printing them.
        return (
            cli.main(
                argv if argv is not None else sys.argv[1:],
                prog_name='workshopify',
                standalone_mode=False,
            )
            or 0
        )
    except WorkshopifyError as e:
        _err(str(e))
        return 1
    except click.ClickException as e:
        _err(_shown(e))
        return e.exit_code
    except click.Abort:
        # Ctrl-C, or EOF at a prompt. click has already turned the
        # KeyboardInterrupt into this and echoed a bare newline of its own on
        # the way; what is left to decide is the status, and 128 + SIGINT is
        # what a shell expects to be told.
        return 130


def _shown(error: click.ClickException) -> str:
    """A usage error as click writes it, captured rather than emitted.

    click formats these better than this file would: `set` missing its second
    argument gets the usage line and the `--help` hint alongside the
    complaint, and each error subclass knows its own shape -- no subcommand at
    all prints the whole help and no `Error:` line, which is right. What is
    not click's to decide is where it goes, so it is written to a buffer and
    handed to `_err` like every other diagnostic. The capitalised `Error:` is
    the price, and it is cheaper than reimplementing the other three lines.
    """
    shown = io.StringIO()
    error.show(shown)
    return shown.getvalue().rstrip('\n')
