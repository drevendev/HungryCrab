"""Public discovery tools consumed by an invoked agent host."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .artifact_read import read_bytes, read_object
from .discovery_session import initialize, live_context, next_action, record, report, stop
from .errors import CrabError


def add_discovery_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "discover", help="bounded receiving-need discovery and source inspection"
    )
    commands = parser.add_subparsers(dest="discover_command", required=True)
    context = commands.add_parser(
        "context", help="print current receiving identity before planning"
    )
    context.add_argument(
        "--maw", type=Path, default=Path(), help="receiving repository (default: .)"
    )
    init = commands.add_parser(
        "init", help="freeze needs and budgets outside the receiving worktree"
    )
    init.add_argument(
        "directory", type=Path, help="new session directory outside the receiving worktree"
    )
    init.add_argument("--maw", type=Path, default=Path(), help="receiving repository (default: .)")
    init.add_argument(
        "--plan", type=Path, required=True, help="frozen receiving needs and resource limits"
    )
    for name in ("next", "record", "report"):
        command = commands.add_parser(
            name,
            help={
                "next": "lease the next bounded action",
                "record": "append the owning host's actual action receipt",
                "report": "show scoped outcomes and unfinished needs",
            }[name],
        )
        command.add_argument("directory", type=Path, help="existing discovery session")
        if name == "record":
            command.add_argument(
                "--input", type=Path, required=True, help="actual tool and usage receipt JSON"
            )
    halt = commands.add_parser("stop", help="retain unfinished work and explicit external blockers")
    halt.add_argument("directory", type=Path, help="existing discovery session")
    halt.add_argument(
        "--reason",
        required=True,
        choices=("usage-unavailable", "external-dependency", "policy-or-legal-dependency"),
        help="actual unresolved dependency; unknown usage cannot become zero",
    )
    for name in ("packet", "search", "rank", "frontier", "history", "issues"):
        command = commands.add_parser(
            name,
            help={
                "packet": "read bounded immutable source/test ranges",
                "search": "execute a bounded provider query batch",
                "rank": "rank marginal need coverage while keeping occurrences",
                "frontier": "qualify one source-supported repository hop under one request budget",
                "history": "read bounded path-specific pinned history and diffs",
                "issues": "acquire selected issue leads as IDEAS_ONLY evidence",
            }[name],
        )
        command.add_argument(
            "--input", type=Path, required=True, help="bounded operation plan JSON"
        )
    probe = commands.add_parser("probe", help="execute an original receiving-owned Python probe")
    probe.add_argument("directory", type=Path, help="new owned output directory outside the maw")
    probe.add_argument(
        "--maw", type=Path, default=Path(), help="trusted receiving repository (default: .)"
    )
    probe.add_argument(
        "--input", type=Path, required=True, help="original source, provenance and controls"
    )


def cmd_discover(args: argparse.Namespace) -> int:
    try:
        return _run_discover(args)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CrabError("invalid discovery operation plan or receipt") from exc


def _run_discover(args: argparse.Namespace) -> int:
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
    elif action == "stop":
        result = stop(args.directory, args.reason)
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
        from .discovery_search import budget_for_plan, search

        plan = read_object(args.input)
        queries = plan.get("queries")
        if not isinstance(queries, list) or not 1 <= len(queries) <= 16:
            raise CrabError("search plan requires 1..16 bounded queries")
        budget = budget_for_plan(plan)
        if plan.get("session") is not None and len(queries) != 1:
            raise CrabError("one leased session search accepts exactly one query")
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
    elif action == "frontier":
        from .discovery_frontier import frontier
        from .discovery_search import budget_for_plan

        plan = read_object(args.input)
        result = frontier(
            plan.get("roots", []),
            budget=budget_for_plan(plan),
            maximum=plan.get("maximum", 8),
        )
    elif action == "history":
        from .discovery_history import history_packet

        plan = read_object(args.input)
        result = history_packet(
            Path(plan["repo"]),
            plan["sha"],
            plan["paths"],
            hypothesis=plan["hypothesis"],
            reason=plan["reason"],
            commits=plan.get("commits", 8),
            byte_budget=plan.get("byte_budget", 131_072),
        )
    elif action == "issues":
        from .cache import Slug
        from .discovery_history import targeted_issues
        from .discovery_search import budget_for_plan
        from .fetch.github import GitHubClient

        plan = read_object(args.input)
        result = targeted_issues(
            Slug.parse(plan["repository"]),
            plan["numbers"],
            hypothesis=plan["hypothesis"],
            reason=plan["reason"],
            budget=budget_for_plan(plan),
            client=GitHubClient(retries=0),
        )
    else:
        raise CrabError("unsupported discovery action")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
