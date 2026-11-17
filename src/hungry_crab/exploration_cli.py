"""Supervised exploration CLI; each write validates one explicit operator/agent record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .artifact_read import read_object
from .exploration import initialize, record, register, report


def add_exploration_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "explore", help="plan and record source exploration beyond the trait menu"
    )
    commands = parser.add_subparsers(dest="explore_command", required=True)
    init = commands.add_parser(
        "init", help="freeze a meal's inputs, goals, policy and original candidate set"
    )
    init.add_argument(
        "--meal-dir", type=Path, required=True, help="fresh single-prey meal directory"
    )
    init.add_argument("--maw", type=Path, default=Path(), help="receiving repository (default: .)")
    init.add_argument(
        "--plan",
        type=Path,
        required=True,
        help="JSON scope, goals, flows, constraints and relevant facets",
    )
    for name, help_text in (
        ("record", "append one validated facet, decision or phase observation"),
        ("register", "register a verified source-linked behavior nutrient in its meal"),
        ("report", "report coverage, pending work and phase-specific observations"),
        ("reconcile-novelty", "retain a later immutable implementation as supersession"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "directory", type=Path, help="immutable exploration directory returned by init"
        )
        if name != "report":
            command.add_argument(
                "--input",
                type=Path,
                required=True,
                help="JSON evidence record authored for this exploration",
            )
        if name == "register":
            command.add_argument(
                "--source-repo",
                type=Path,
                required=True,
                help="inert Git object store containing the donor's pinned commit",
            )


def cmd_explore(args: argparse.Namespace) -> int:
    if args.explore_command == "init":
        result: object = {
            "directory": str(
                initialize(args.meal_dir, read_object(args.plan), maw=args.maw.resolve())
            )
        }
    elif args.explore_command == "record":
        result = record(args.directory, read_object(args.input))
    elif args.explore_command == "register":
        result = register(args.directory, read_object(args.input), source_repo=args.source_repo)
    elif args.explore_command == "reconcile-novelty":
        from .novelty_exclusions import reconcile

        result = reconcile(args.directory, read_object(args.input))
    else:
        result = report(args.directory)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0
