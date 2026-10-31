"""Public discovery tools consumed by an invoked agent host."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .artifact_read import read_bytes, read_object
from .discovery_session import initialize, live_context, next_action, record, report
from .errors import CrabError


def add_discovery_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "discover", help="bounded receiving-need discovery and source inspection"
    )
    commands = parser.add_subparsers(dest="discover_command", required=True)
    context = commands.add_parser(
        "context", help="print current receiving identity before planning"
    )
    context.add_argument("--maw", type=Path, default=Path())
    init = commands.add_parser(
        "init", help="freeze needs and budgets outside the receiving worktree"
    )
    init.add_argument("directory", type=Path)
    init.add_argument("--maw", type=Path, default=Path())
    init.add_argument("--plan", type=Path, required=True)
    for name in ("next", "record", "report"):
        command = commands.add_parser(name)
        command.add_argument("directory", type=Path)
        if name == "record":
            command.add_argument("--input", type=Path, required=True)
    for name in ("packet", "search", "rank"):
        command = commands.add_parser(name)
        command.add_argument("--input", type=Path, required=True)
    probe = commands.add_parser("probe", help="execute an original receiving-owned Python probe")
    probe.add_argument("directory", type=Path)
    probe.add_argument("--maw", type=Path, default=Path())
    probe.add_argument(
        "--input", type=Path, required=True, help="original source, provenance and controls"
    )


def cmd_discover(args: argparse.Namespace) -> int:
    action = args.discover_command
    result: Any
    if action == "context":
        result = live_context(args.maw.resolve())
    elif action == "init":
        result = initialize(args.directory, args.maw.resolve(), read_object(args.plan))
    elif action == "next":
        result = next_action(args.directory)
    elif action == "record":
        result = record(args.directory, read_object(args.input))
    elif action == "report":
        result = report(args.directory)
    elif action == "packet":
        from .source_inspection import source_packet

        plan = read_object(args.input)
        try:
            result = source_packet(
                Path(plan["repo"]),
                plan["sha"],
                plan["paths"],
                plan.get("queries", []),
                need=plan["need"],
                source_url=plan.get("source_url"),
                byte_budget=plan.get("byte_budget", 131_072),
                related=plan.get("related", True),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CrabError("invalid bounded source packet plan") from exc
    elif action == "search":
        from .discovery_search import SearchBudget, search

        plan = read_object(args.input)
        queries = plan.get("queries")
        if not isinstance(queries, list) or not 1 <= len(queries) <= 16:
            raise CrabError("search plan requires 1..16 bounded queries")
        budget = SearchBudget(maximum=plan.get("request_budget", 16))
        results = []
        for query in queries:
            if not isinstance(query, dict):
                raise CrabError("each search query must be an object")
            results.append(
                search(
                    query.get("query", ""),
                    provider=query.get("provider", "github"),
                    surface=query.get("surface", "repository"),
                    budget=budget,
                    limit=plan.get("limit", 20),
                )
            )
        result = {
            "results": results,
            "requests": budget.used,
            "limit": budget.maximum,
            "coverage": "see-each-provider",
            "independent_invocation": True,
        }
    elif action == "rank":
        from .discovery_search import rank_cohort

        plan = read_object(args.input)
        result = rank_cohort(
            plan.get("candidates", []),
            limit=plan.get("limit", 10),
            seen=set(plan.get("prior_exposure", [])),
        )
    elif action == "probe":
        from .receiving_probe import run_probe

        plan = read_object(args.input)
        source = read_bytes(Path(plan["source"]), 65_536).decode("utf-8")
        path = run_probe(
            args.directory,
            args.maw.resolve(),
            source,
            author=plan.get("author", ""),
            expected=plan.get("expected", ""),
            controls=plan.get("controls", []),
            original_receiving_owned=plan.get("original_receiving_owned") is True,
            timeout=plan.get("timeout", 30),
        )
        result = {"receipt": str(path), "result": read_object(path)}
    else:
        raise CrabError("unsupported discovery action")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
