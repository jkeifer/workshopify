"""The one command-line surface, and the one place a failure becomes an exit.

Every module below raises WorkshopifyError carrying the full diagnostic;
main() prints it to stderr and returns 1. Nothing else in the package may
exit the process, and nothing else may print: the modules below return what
they computed, and every line the user sees is rendered here.
"""

from __future__ import annotations

import argparse
import sys

from pathlib import Path

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


def _render(args: argparse.Namespace, config: Config) -> int:
    changed = render.apply(config, context.load(config))
    _err(*(f'rendered {config.display_path(path)}' for path in changed))
    return 0


def _check(args: argparse.Namespace, config: Config) -> int:
    errors = render.check(config, context.load(config))
    _err(*errors)
    if errors:
        return 1
    _err('rendered values and assets are current')
    return 0


def _generate(args: argparse.Namespace, config: Config) -> int:
    written = notebooks.generate(
        config,
        context.load(config),
        args.output_dir or config.root,
    )
    _err(*(f'wrote {config.display_path(path)}' for path in written))
    return 0


def _build(args: argparse.Namespace, config: Config) -> int:
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
        clean_first=args.clean,
        overwrite_dirty=args.overwrite_dirty,
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


def _set(args: argparse.Namespace, config: Config) -> int:
    rewritten = params.set_param(config, args.key, args.value)
    _err(
        *(f'updated {config.display_path(path)}' for path in rewritten.paths),
        # The recorded value, not the argument: they differ in type whenever
        # the param is not a string, and the file now holds the former.
        f'set {args.key} = {rewritten.value!r}',
    )
    return 0


def _worktree(args: argparse.Namespace, config: Config) -> int:
    path = args.path or config.root / args.branch
    prepared = worktree.prepare_worktree(
        config.root,
        args.branch,
        path,
        orphan=args.orphan,
    )
    _err(*prepared.notes)
    # The path is the command's result, so it goes to stdout where it can be
    # piped; the notes above are commentary and stay on stderr.
    print(prepared.path)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='workshopify',
        description=(
            'Build a template-rendered, notebook-based workshop repository. '
            'The repo declares everything mechanical in [tool.workshopify] '
            'tables in its pyproject.toml; these commands do the rest.'
        ),
    )
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser(
        'render',
        help='re-render templated values and assets in place',
    ).set_defaults(func=_render)
    sub.add_parser(
        'check',
        help='report stale rendered values or assets; exit 1 if any',
    ).set_defaults(func=_check)

    generate = sub.add_parser(
        'generate',
        help='generate the notebooks and notes from src/',
    )
    generate.add_argument('--output-dir', type=Path, default=None)
    generate.set_defaults(func=_generate)

    build_cmd = sub.add_parser(
        'build',
        help='assemble the published workshop tree into its branch worktree',
    )
    build_cmd.add_argument('--clean', action='store_true')
    build_cmd.add_argument('--overwrite-dirty', action='store_true')
    build_cmd.set_defaults(func=_build)

    set_cmd = sub.add_parser(
        'set',
        help='set a [tool.workshopify.params] value and re-render',
    )
    set_cmd.add_argument('key')
    set_cmd.add_argument('value')
    set_cmd.set_defaults(func=_set)

    wt = sub.add_parser('worktree', help='prepare a branch worktree without building')
    wt.add_argument('branch')
    wt.add_argument('--path', type=Path)
    wt.add_argument('--orphan', action='store_true')
    wt.set_defaults(func=_worktree)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        # Every subcommand works from the discovered repo, so it is found once
        # here -- inside the try, because failing to find one is the first
        # diagnostic a user outside a workshop repo gets.
        return args.func(args, Config.discover())
    except WorkshopifyError as e:
        _err(str(e))
        return 1
