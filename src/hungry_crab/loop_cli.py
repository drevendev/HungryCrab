"""Scheduler-neutral CLI contract; one invocation never runs a model or starts a scheduler."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .loop import PHASES, Loop, read_json
from .loop_provider import GitHubLoopProvider
from .loop_work import publish, serve_phase, tag
from .maw import MawConfig, maw_slug
from .serve import GhIssueClient


def add_loop_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("loop", help="run one persisted phase per scheduler wake-up")
    actions = parser.add_subparsers(dest="loop_command", required=True)
    for name in (
        "init",
        "status",
        "next",
        "record",
        "pause",
        "resume",
        "acknowledge",
        "serve",
        "publish",
        "tag",
        "metrics",
    ):
        command = actions.add_parser(name)
        command.add_argument("--maw", type=Path, default=Path())
        command.add_argument(
            "--control",
            type=Path,
            default=None,
            help="state in a control repository; maw-owned policy remains authoritative",
        )
        command.add_argument("--json", action="store_true")
        if name in {"record", "serve", "publish", "tag"}:
            command.add_argument("--token", required=True, help="lease token from loop next")
        if name == "record":
            command.add_argument("--phase", required=True, choices=PHASES)
            command.add_argument("--result", required=True, choices=("ok", "fail", "skip"))
            command.add_argument("--note", default="")
            command.add_argument("--receipt", type=Path)
        if name == "acknowledge":
            command.add_argument(
                "--drop-pr", default=None, help="human rejection of this round's GROW PR"
            )
            command.add_argument(
                "--skip-work",
                action="store_true",
                help="human decision to end a round without implementation",
            )
        if name == "serve":
            command.add_argument("--prey", required=True)
            command.add_argument("--id", required=True, dest="nutrient_id")
            command.add_argument(
                "--receipt",
                type=Path,
                help="existing clean-room or COPY materialization receipt for GROW",
            )
        if name == "publish":
            command.add_argument("--head", required=True, help="immutable local commit SHA")
            command.add_argument("--title", required=True)
            command.add_argument("--body-file", type=Path, required=True)
            command.add_argument("--receipt", type=Path, required=True)


def cmd_loop(args: argparse.Namespace) -> int:
    maw = args.maw.resolve()
    slug = maw_slug(maw)
    config = MawConfig.load(maw)
    provider = None

    def run(*command: str) -> str:
        return GhIssueClient(token_env=config.serve.token_env).run_gh(*command)

    if slug:
        provider = GitHubLoopProvider(slug, run)
    loop = Loop(maw, args.control, provider=provider)
    action = args.loop_command
    if action == "init":
        result = loop.init()
    elif action in {"status", "metrics"}:
        result = loop.status()
        if action == "metrics":
            result = {
                "schema": "hungry-crab.loop-metrics/1",
                "maw": result["maw"],
                "history": result["history"],
            }
    elif action == "next":
        result = loop.next()
    elif action == "record":
        result = loop.record(
            args.token,
            args.phase,
            args.result,
            note=args.note,
            receipt=read_json(args.receipt) if args.receipt else None,
        )
    elif action in {"pause", "resume"}:
        result = loop.pause(action == "pause")
    elif action == "acknowledge":
        result = loop.acknowledge(skip_work=args.skip_work, drop_pr=args.drop_pr)
    elif action == "serve":
        result = serve_phase(
            loop,
            args.token,
            args.prey,
            args.nutrient_id,
            receipt_payload=args.receipt.read_text(encoding="utf-8") if args.receipt else None,
            cache_dir=args.cache_dir,
        )
    elif action == "publish":
        result = publish(
            loop,
            args.token,
            args.head,
            args.title,
            args.body_file.read_text(encoding="utf-8"),
            read_json(args.receipt),
            run,
        )
    else:
        result = tag(loop, args.token)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if "phase" in result and "round" in result:
            print(f"round {result['round']}: {result['phase']}")
        print(json.dumps(result, ensure_ascii=False))
    return 0
