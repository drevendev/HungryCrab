"""Public discovery tools consumed by an invoked agent host."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .artifact_read import read_bytes, read_object
from .discovery_session import (
    initialize,
    live_context,
    next_action,
    record,
    record_usage,
    report,
    stop,
)
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
    for name in ("next", "record", "usage", "report"):
        command = commands.add_parser(
            name,
            help={
                "next": "lease the next bounded action",
                "record": "append the owning host's actual action receipt",
                "usage": "settle actual host model use without advancing a semantic phase",
                "report": "show scoped outcomes and unfinished needs",
            }[name],
        )
        command.add_argument("directory", type=Path, help="existing discovery session")
        if name in {"record", "usage"}:
            command.add_argument(
                "--input", type=Path, required=True, help="actual tool and usage receipt JSON"
            )
    halt = commands.add_parser(
        "stop", help="retain unfinished work and actual bounded stop reasons"
    )
    halt.add_argument("directory", type=Path, help="existing discovery session")
    halt.add_argument(
        "--reason",
        required=True,
        choices=(
            "usage-unavailable",
            "external-dependency",
            "policy-or-legal-dependency",
            "model-budget-reserve",
        ),
        help="actual dependency or next-call reserve; unknown usage cannot become zero",
    )
    for name in (
        "index",
        "find",
        "view",
        "packet",
        "search",
        "rank",
        "frontier",
        "history",
        "issues",
    ):
        command = commands.add_parser(
            name,
            help={
                "index": "list real pinned source/test paths and declaration ranges",
                "find": "find bounded literal source locations before selecting exact windows",
                "view": "page an existing inert source artifact without discarding large fields",
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
    for name in ("inspect", "witnesses", "contract", "citations"):
        command = commands.add_parser(
            name, help="cumulative actual source witnesses and typed host contracts"
        )
        command.add_argument("directory", type=Path, help="existing receiving-bound exploration")
        if name != "witnesses":
            command.add_argument(
                "--input",
                type=Path,
                required=True,
                help="typed bounded inspection or citation plan",
            )
    probe = commands.add_parser("probe", help="execute an original receiving-owned Python probe")
    probe.add_argument("directory", type=Path, help="new owned output directory outside the maw")
    probe.add_argument(
        "--maw", type=Path, default=Path(), help="trusted receiving repository (default: .)"
    )
    probe.add_argument(
        "--input", type=Path, required=True, help="original source, provenance and controls"
    )
    cases = commands.add_parser(
        "cases", help="execute original JSON receiving cases and positive controls"
    )
    cases.add_argument("directory", type=Path, help="new owned output directory outside the maw")
    cases.add_argument("--maw", type=Path, default=Path(), help="trusted receiving repository")
    cases.add_argument("--input", type=Path, required=True, help="original JSON cases and controls")
    results = commands.add_parser(
        "case-results", help="project verified receiving calls and actual check observations"
    )
    results.add_argument("receipt", type=Path, help="existing executed receiving-case receipt")
    results.add_argument("--maw", type=Path, default=Path(), help="current receiving repository")
    results.add_argument(
        "--value-bytes", type=int, default=512, help="maximum displayed JSON bytes per value"
    )
    novelty = commands.add_parser(
        "novelty", help="read selected/default/confirmed pending implementation scope"
    )
    novelty.add_argument(
        "--maw", type=Path, default=Path(), help="receiving repository and objects"
    )
    novelty.add_argument(
        "--input", type=Path, required=True, help="explicit implementation scope and pins"
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
    elif action == "usage":
        result = record_usage(args.directory, read_object(args.input))
    elif action == "report":
        result = report(args.directory)
    elif action == "stop":
        result = stop(args.directory, args.reason)
    elif action == "view":
        from .source_view import evidence_page

        plan = read_object(args.input)
        result = evidence_page(
            read_object(Path(plan["artifact"])),
            budget=plan.get("budget", 1800),
            cursor=plan.get("cursor"),
        )
    elif action in {"inspect", "witnesses", "contract", "citations"}:
        from .inspection import contract, inspect, resolve, witnesses

        if action == "witnesses":
            result = witnesses(args.directory)
        else:
            plan = read_object(args.input)
            if action == "inspect":
                result = inspect(args.directory, plan)
            elif action == "contract":
                result = contract(args.directory, terms=plan.get("terms"))
            else:
                result = resolve(args.directory, plan)
    elif action == "find":
        from .source_find import find_source

        plan = read_object(args.input)
        result = find_source(
            Path(plan["repo"]),
            plan["sha"],
            plan["terms"],
            prefix=plan.get("prefix", ""),
            max_files=plan.get("max_files", 64),
            max_bytes=plan.get("max_bytes", 524288),
            limit=plan.get("limit", 12),
        )
    elif action == "index":
        from .source_inspection import source_index

        plan = read_object(args.input)
        result = source_index(
            Path(plan["repo"]),
            plan["sha"],
            need=plan["need"],
            terms=plan.get("terms", []),
            prefix=plan.get("prefix", ""),
            limit=plan.get("limit", 24),
            symbol_files=plan.get("symbol_files", 8),
        )
    elif action == "packet":
        from .source_inspection import source_index, source_packet

        plan = read_object(args.input)
        try:
            navigation = None
            paths = plan.get("paths")
            queries = plan.get("queries", [])
            ranges = plan.get("ranges")
            if paths is None and isinstance(ranges, list):
                paths = list(dict.fromkeys(row["path"] for row in ranges))
            if paths is None:
                terms = plan.get("terms")
                if not terms:
                    raise CrabError(
                        "automatic source selection requires bounded literal path terms"
                    )
                navigation = source_index(
                    Path(plan["repo"]),
                    plan["sha"],
                    need=plan["need"],
                    terms=terms,
                    prefix=plan.get("prefix", ""),
                    limit=4,
                    symbol_files=4,
                )
                paths = [row["path"] for row in navigation["files"]]
                if not queries:
                    queries = [
                        {
                            "kind": symbol["kind"],
                            "name": symbol.get("qualified_name", symbol["name"])
                            if len(symbol.get("qualified_name", symbol["name"])) <= 100
                            else symbol["name"],
                        }
                        for row in navigation["files"]
                        for symbol in row["symbols"]
                        if any(
                            term.casefold()
                            in symbol.get("qualified_name", symbol["name"]).casefold()
                            for term in terms
                        )
                    ][:8] or [{"kind": "function"}, {"kind": "class"}]
            result = source_packet(
                Path(plan["repo"]),
                plan["sha"],
                paths,
                queries,
                need=plan["need"],
                source_url=plan.get("source_url"),
                byte_budget=plan.get("byte_budget", 131_072),
                related=plan.get("related", True),
                ranges=ranges,
            )
            if navigation is not None:
                result["navigation"] = navigation
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
    elif action == "case-results":
        from .receiving_cases import project_results

        result = project_results(args.receipt, args.maw.resolve(), value_bytes=args.value_bytes)
    elif action == "novelty":
        from .novelty import implementation_scope

        result = implementation_scope(args.maw.resolve(), read_object(args.input))
    elif action == "cases":
        from .receiving_cases import run_cases

        path = run_cases(args.directory, args.maw.resolve(), read_object(args.input))
        result = {"receipt": str(path), "result": read_object(path)}
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
            import_receiving=plan.get("import_receiving", False),
            required_receiving_calls=plan.get("required_receiving_calls"),
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
