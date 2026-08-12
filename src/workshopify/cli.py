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


def _render(args: argparse.Namespace) -> int:
    config = Config.discover()
    changed = render.apply(config, context.load(config))
    for path in changed:
        print(f'rendered {config.display_path(path)}', file=sys.stderr)
    return 0


def _check(args: argparse.Namespace) -> int:
    config = Config.discover()
    errors = render.check(config, context.load(config))
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print('rendered values and assets are current', file=sys.stderr)
    return 0


def _generate(args: argparse.Namespace) -> int:
    config = Config.discover()
    written = notebooks.generate(
        config,
        context.load(config),
        args.output_dir or config.root,
    )
    for path in written:
        print(f'wrote {config.display_path(path)}', file=sys.stderr)
    return 0


def _build(args: argparse.Namespace) -> int:
    config = Config.discover()
    prepared = worktree.prepare_worktree(
        config.root,
        config.build.branch,
        config.root / config.build.branch,
        orphan=False,
    )
    for note in prepared.notes:
        print(note, file=sys.stderr)
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
    print(f'built into {target}', file=sys.stderr)
    print(f'review with: git -C {target} status', file=sys.stderr)
    print('publish with:', file=sys.stderr)
    print(f'  git -C {target} add -A', file=sys.stderr)
    print(
        f'  PREK_ALLOW_NO_CONFIG=1 git -C {target} commit -m "Update notebooks"',
        file=sys.stderr,
    )
    print(f'  git -C {target} push', file=sys.stderr)
    print(
        'PREK_ALLOW_NO_CONFIG=1 is required: git hooks are shared across all '
        'worktrees,\n'
        'and the workshop branch intentionally carries no pre-commit config.',
        file=sys.stderr,
    )


def _stale_report(stale: list[Path]) -> None:
    """Tracked files the build did not write, and how to be rid of them."""
    if not stale:
        return
    print('not written by this build:', file=sys.stderr)
    for path in stale:
        print(f'  {path}', file=sys.stderr)
    print('re-run with --clean to remove', file=sys.stderr)


def _set(args: argparse.Namespace) -> int:
    config = Config.discover()
    changed = params.set_param(config, args.key, args.value)
    for path in changed:
        print(f'updated {config.display_path(path)}', file=sys.stderr)
    print(f'set {args.key} = {args.value!r}', file=sys.stderr)
    return 0


def _worktree(args: argparse.Namespace) -> int:
    config = Config.discover()
    path = args.path or config.root / args.branch
    prepared = worktree.prepare_worktree(config.root, args.branch, path, args.orphan)
    for note in prepared.notes:
        print(note, file=sys.stderr)
    # The path is the command's result, so it goes to stdout where it can be
    # piped; the notes above are commentary and stay on stderr.
    print(prepared.path)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='workshopify', description=__doc__)
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
        return args.func(args)
    except WorkshopifyError as e:
        print(str(e), file=sys.stderr)
        return 1
