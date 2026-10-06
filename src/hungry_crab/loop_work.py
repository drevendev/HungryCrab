"""Bounded loop effects. GROW keeps the existing licensed publication transaction.

MOLT and HARDEN publish an immutable, fully scanned Git tree to a dedicated branch. They never
check out that tree, run its hooks, push the default branch, or merge a pull request.
"""

from __future__ import annotations

import fnmatch
import math
import re
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from .cache import Slug, prey_paths
from .errors import CrabError
from .fetch.git import GitRunner
from .ledger import Ledger
from .loop import Loop, _text, ci_state, state_lock
from .pr_publication import PreparedPullRequest
from .publication_safety import format_publication_findings, scan_publication_bundle
from .serve import GhIssueClient, ServeOptions, serve


def check_paths(loop: Loop, paths: list[str]) -> None:
    seen: set[str] = set()
    for path in paths:
        parts = path.split("/")
        folded = path.casefold()
        if (
            not path
            or "\\" in path
            or ":" in path
            or path.startswith("/")
            or any(part in {"", ".", ".."} for part in parts)
            or any(ord(character) < 32 for character in path)
            or folded in seen
            or any(part.casefold() == ".git" for part in parts)
        ):
            raise CrabError("loop publication contains an unsafe or duplicate path")
        seen.add(folded)
        if folded.startswith((".crab/loop", ".crab/maws")) or any(
            fnmatch.fnmatchcase(folded, pattern.casefold())
            or folded == pattern.casefold().removesuffix("/**")
            for pattern in loop.settings.protected
        ):
            raise CrabError(f"loop refuses protected path: {path}")


def check_push_target(loop: Loop, git: GitRunner) -> None:
    urls = git.run("remote", "get-url", "--push", "--all", "origin").splitlines()
    if len(urls) != 1 or Slug.parse(urls[0]) != loop.slug:
        raise CrabError("loop requires exactly one origin push URL matching the maw")


def serve_phase(
    loop: Loop,
    token: str,
    prey: str,
    nutrient_id: str,
    *,
    receipt_payload: str | None = None,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    with state_lock(loop.path):
        data = loop._load()
        loop.active(data, token)
        phase = data["phase"]
        if phase not in {"serve", "grow"}:
            raise CrabError("loop serve is available only during SERVE or GROW")
        meal = next((row for row in data["meals"] if row["prey"] == prey), None)
        if meal is None:
            raise CrabError("choose a prey with an accepted EAT receipt in this round")
        issues, prs = loop._provider().counts()
        if (phase == "serve" and issues >= loop.settings.open_issues_max) or (
            phase == "grow" and prs >= loop.settings.open_prs_max
        ):
            raise CrabError("open artifact budget exhausted")
        if phase == "grow" and any(
            row["round"] == data["round"] and row["phase"] == "grow" for row in data["prs"]
        ):
            raise CrabError("GROW publishes at most one nutrient per round")
        ledger = Ledger.load(loop.config.ledger_path(cache_dir), maw=loop.maw.name)
        if phase == "grow":
            entry = ledger.entries.get(nutrient_id)
            if entry is None or not entry.url:
                raise CrabError("GROW requires a served nutrient with a persistent ledger receipt")
            if "/issues/" in entry.url:
                issue = loop._provider().artifact(entry.url)
                if entry.url not in data["issues"] or not str(issue.get("body", "")).startswith(
                    f"<!-- {nutrient_id} -->"
                ):
                    raise CrabError(
                        "GROW requires an issue served by this round for the selected nutrient"
                    )
                # Issue serving is terminal for the ordinary menu protocol. Here the explicit
                # round transition authorizes implementation, only in this in-memory ledger.
                entry.status = "accepted"
        config = replace(
            loop.config,
            serve=replace(
                loop.config.serve, max_prs_per_run=min(1, loop.config.serve.max_prs_per_run)
            ),
        )

        def guard(prepared: PreparedPullRequest) -> None:
            check_paths(loop, [file.path for file in prepared.files])

        client = GhIssueClient(token_env=config.serve.token_env)
        if phase == "grow":
            check_push_target(loop, GitRunner(loop.maw))
        report = serve(
            Path(meal["meal"]).parent,
            loop.maw,
            ServeOptions(
                ids=[nutrient_id],
                mode="issue" if phase == "serve" else "pr-branch",
                notes=Path(meal["notes"]),
            ),
            config=config,
            ledger=ledger,
            client=client,
            now=loop.now,
            receipt_payloads={nutrient_id: receipt_payload} if receipt_payload else {},
            prey_repo=prey_paths(Slug.parse(prey), cache_dir).repo,
            publication_guard=guard,
            slug_lookup=lambda _: loop.slug,
        )
        entry = ledger.entries.get(nutrient_id)
        if entry is None or not entry.url:
            raise CrabError("no artifact was published or reconciled; inspect the serve report")
        truth = loop._provider().artifact(entry.url)
        if phase == "serve":
            if "/issues/" not in entry.url:
                raise CrabError("SERVE did not reconcile an issue")
            data["issues"] = list(dict.fromkeys([*data["issues"], entry.url]))
        else:
            if "/pull/" not in entry.url:
                raise CrabError("GROW did not reconcile a PR")
            files = loop._provider().files(entry.url)
            check_paths(loop, [row["filename"] for row in files])
            data["prs"].append(
                {
                    "round": data["round"],
                    "phase": phase,
                    "url": entry.url,
                    "sha": truth["head"]["sha"],
                    "state": "open",
                    "nutrient_id": nutrient_id,
                    "files": files,
                }
            )
            data["waiting_on"] = {"kind": "ci", "reason": "waiting for GROW CI"}
        loop._save(data)
        return report.to_dict()


def _changes(git: GitRunner, base: str, head: str) -> list[tuple[str, str]]:
    items = git.run("diff", "--name-status", "--no-renames", "-z", base, head, "--").split("\0")
    items = [item for item in items if item]
    if not items or len(items) % 2:
        raise CrabError("loop work must have a non-empty, readable changed-path set")
    changes = list(zip(items[::2], items[1::2], strict=True))
    if any(status not in {"A", "M", "D"} for status, _ in changes):
        raise CrabError("loop refuses type changes and unsupported Git objects")
    return changes


def _blob(git: GitRunner, commit: str, path: str) -> str:
    listing = git.run("ls-tree", "-z", commit, "--", path)
    if not listing.startswith(("100644 blob ", "100755 blob ")):
        raise CrabError(f"loop work requires regular text files: {path}")
    content = git.run("cat-file", "blob", f"{commit}:{path}")
    if "\ufffd" in content or any(ord(char) < 32 and char not in "\t\r\n" for char in content):
        raise CrabError(f"loop publication requires losslessly decoded UTF-8 text: {path}")
    return content


def _molt(
    loop: Loop, data: dict[str, Any], changes: list[tuple[str, str]], receipt: dict[str, Any]
) -> None:
    landed = loop._landed(data)
    if not landed:
        raise CrabError("MOLT is skipped when this round landed nothing")
    checks = receipt.get("checks", {})
    if not isinstance(checks, dict):
        raise CrabError("MOLT checks must be an object")
    for key in ("lint_before", "lint_after", "coverage_before", "coverage_after"):
        value = checks.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value < 0
            or not math.isfinite(value)
        ):
            raise CrabError("MOLT requires measured before/after lint and coverage evidence")
    if any(type(checks[key]) is not int for key in ("lint_before", "lint_after")) or any(
        checks[key] > 100 for key in ("coverage_before", "coverage_after")
    ):
        raise CrabError("MOLT lint counts must be integers and coverage percentages must be <= 100")
    unreachable = receipt.get("unreachable", {})
    if not isinstance(unreachable, dict):
        raise CrabError("MOLT unreachability evidence must be an object")
    if (
        checks.get("tests_passed") is not True
        or checks["lint_after"] > checks["lint_before"]
        or checks["coverage_after"] < checks["coverage_before"]
        or checks.get("public_surface_added") is not False
    ):
        raise CrabError("MOLT violates tests, lint, coverage or public-surface invariants")
    introduced = {
        row["filename"] for pr in landed for row in pr["files"] if row["status"] == "added"
    }
    round_paths = {row["filename"] for pr in landed for row in pr["files"]}
    if any(path not in round_paths for _, path in changes):
        raise CrabError("MOLT can change only paths landed in this round")
    for status, path in changes:
        if status == "D" and (
            path not in introduced
            or not isinstance(unreachable.get(path), str)
            or not unreachable[path].strip()
        ):
            raise CrabError(
                "MOLT deletion needs a round-introduced path and unreachability evidence"
            )


def _release(
    loop: Loop,
    data: dict[str, Any],
    git: GitRunner,
    base: str,
    head: str,
    changes: list[tuple[str, str]],
    receipt: dict[str, Any],
) -> None:
    landed = loop._landed(data)
    if not landed:
        raise CrabError("HARDEN is skipped when this round landed nothing")
    if any(pr["round"] == data["round"] and pr["state"] == "open" for pr in data["prs"]):
        raise CrabError("HARDEN waits for every round PR to be merged or closed")
    if receipt.get("contract_changed") is not False:
        raise CrabError("contract or schema changes require human release planning")
    before = _version(_text(receipt, "previous_version"))
    after = _version(_text(receipt, "version"))
    nutrient = any(pr["phase"] == "grow" for pr in landed)
    expected = (before[0], before[1] + 1, 0) if nutrient else (before[0], before[1], before[2] + 1)
    if after != expected:
        raise CrabError("HARDEN requires a minor bump for nutrients or patch bump for MOLT alone")
    paths = {path for _, path in changes}
    if (
        "CHANGELOG.md" not in paths
        or paths - set(loop.settings.release_files)
        or any(status == "D" for status, _ in changes)
    ):
        raise CrabError("HARDEN requires CHANGELOG.md and only maw-authorized release files")
    changelog = _blob(git, head, "CHANGELOG.md")
    if receipt["version"] not in changelog or any(
        pr["url"] not in changelog or (pr.get("nutrient_id") and pr["nutrient_id"] not in changelog)
        for pr in landed
    ):
        raise CrabError("HARDEN changelog must trace every landed PR and nutrient ID")
    for path in paths - {"CHANGELOG.md"}:
        old, new = _blob(git, base, path), _blob(git, head, path)
        if receipt["previous_version"] not in old or receipt["version"] not in new:
            raise CrabError("HARDEN version files must contain the declared before/after versions")


def _version(value: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise CrabError("HARDEN versions must be stable MAJOR.MINOR.PATCH")
    major, minor, patch = map(int, value.split("."))
    return major, minor, patch


def publish(
    loop: Loop,
    token: str,
    head: str,
    title: str,
    body: str,
    receipt: dict[str, Any],
    run: Callable[..., str],
) -> dict[str, Any]:
    with state_lock(loop.path):
        data = loop._load()
        loop.active(data, token)
        phase = data["phase"]
        if phase not in {"molt", "harden"} or loop.slug is None:
            raise CrabError(
                "loop publish requires MOLT or HARDEN in a GitHub maw; GROW uses loop serve"
            )
        if not title.strip() or not body.strip():
            raise CrabError("loop PR requires a title and a reviewable body")
        git = GitRunner(loop.maw)
        check_push_target(loop, git)
        if git.toplevel() != loop.maw:
            raise CrabError("loop publication requires the maw repository root")
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
            raise CrabError("loop publish requires an immutable commit SHA")
        base = run("api", f"repos/{loop.slug}", "--jq", ".default_branch").strip()
        branch = loop.branch(data)
        if branch == base:
            raise CrabError("refusing to publish a loop phase to the maw's default branch")
        git.run("check-ref-format", "--branch", base)
        git.run("fetch", "--no-tags", "origin", f"refs/heads/{base}")
        base_sha = git.run("rev-parse", "FETCH_HEAD").strip()
        if git.try_run("merge-base", "--is-ancestor", base_sha, head) is None:
            raise CrabError("loop source commit must include the current default branch")
        changes = _changes(git, base_sha, head)
        check_paths(loop, [path for _, path in changes])
        declared = receipt.get("changed_paths")
        if not isinstance(declared, list) or set(declared) != {path for _, path in changes}:
            raise CrabError("loop work receipt must declare the exact committed changed-path set")
        if phase == "molt":
            _molt(loop, data, changes, receipt)
        else:
            _release(loop, data, git, base_sha, head, changes, receipt)
        marker = loop.marker(data)
        body = f"{marker}\n{body}"
        findings = scan_publication_bundle(
            [
                ("<title>", title),
                ("<body>", body),
                *(
                    (path, _blob(git, base_sha if status == "D" else head, path))
                    for status, path in changes
                ),
            ]
        )
        if findings:
            raise CrabError(format_publication_findings(findings))
        existing = loop._provider().find_pr(marker)
        tree = git.run("rev-parse", f"{head}^{{tree}}").strip()
        if existing is None:
            _, prs = loop._provider().counts()
            if prs >= loop.settings.open_prs_max:
                raise CrabError("open PR budget exhausted")
            remote = git.run("ls-remote", "--heads", "origin", f"refs/heads/{branch}").strip()
            if remote:
                git.run("fetch", "--no-tags", "origin", f"refs/heads/{branch}")
                if git.run("rev-parse", "FETCH_HEAD^{tree}").strip() != tree:
                    raise CrabError(
                        "existing loop branch contains a different payload; human recovery required"
                    )
            else:
                commit = git.run(
                    "-c", "commit.gpgsign=false", "commit-tree", tree, "-p", base_sha, "-m", title
                ).strip()
                git.run("-c", "core.hooksPath=", "push", "origin", f"{commit}:refs/heads/{branch}")
            with tempfile.TemporaryDirectory(prefix="crab-loop-") as scratch:
                body_path = Path(scratch) / "body.md"
                body_path.write_text(body, encoding="utf-8")
                url = run(
                    "pr",
                    "create",
                    "--repo",
                    str(loop.slug),
                    "--head",
                    branch,
                    "--base",
                    base,
                    "--title",
                    title,
                    "--body-file",
                    str(body_path),
                ).strip()
            existing = loop._provider().artifact(url)
        if existing.get("state") != "open" or not str(existing.get("body", "")).startswith(marker):
            raise CrabError("loop phase PR is closed or has a different marker; reconcile manually")
        url = existing["html_url"]
        if existing["head"].get("ref") != branch or existing.get("base", {}).get("ref") != base:
            raise CrabError("provider PR has a different loop branch or default base")
        git.run("fetch", "--no-tags", "origin", existing["head"]["sha"])
        if git.run("rev-parse", "FETCH_HEAD^{tree}").strip() != tree:
            raise CrabError("provider PR content differs from the scanned tree")
        files = loop._provider().files(url)
        check_paths(loop, [row["filename"] for row in files])
        if {row["filename"] for row in files} != {path for _, path in changes}:
            raise CrabError("provider PR does not match the complete publication payload")
        pr = {
            "round": data["round"],
            "phase": phase,
            "url": url,
            "sha": existing["head"]["sha"],
            "state": "open",
            "files": files,
            "receipt": receipt,
            "base": base,
        }
        data["prs"] = [
            row
            for row in data["prs"]
            if not (row["phase"] == phase and row["round"] == data["round"])
        ] + [pr]
        data["waiting_on"] = {"kind": "merge", "reason": f"waiting for human {phase.upper()} merge"}
        if phase == "harden":
            loop._event(
                data,
                "published",
                "release PR awaits human merge",
                {**receipt, "url": url, "sha": pr["sha"]},
            )
            data["active"] = None
            data["attempt"] = 0
        loop._save(data)
        return pr


def tag(loop: Loop, token: str) -> dict[str, Any]:
    with state_lock(loop.path):
        data = loop._load()
        loop.active(data, token, "harden")
        pr = next(
            (
                row
                for row in data["prs"]
                if row["round"] == data["round"] and row["phase"] == "harden"
            ),
            None,
        )
        if pr is None:
            raise CrabError("no release PR to harden")
        truth = loop._provider().artifact(pr["url"])
        if not truth.get("merged_at") or truth["head"]["sha"] != pr["sha"]:
            raise CrabError("release PR must be merged at the reviewed head before tagging")
        if ci_state(truth) != "pass":
            raise CrabError("release PR must have passing CI at the reviewed head before tagging")
        sha = truth["merge_commit_sha"]
        if truth.get("base", {}).get("ref") != pr.get("base"):
            raise CrabError("release PR base changed since publication")
        name = "v" + _text(pr["receipt"], "version")
        git = GitRunner(loop.maw)
        check_push_target(loop, git)
        git.run("fetch", "--no-tags", "origin", f"refs/heads/{pr['base']}")
        default_sha = git.run("rev-parse", "FETCH_HEAD").strip()
        if git.try_run("merge-base", "--is-ancestor", sha, default_sha) is None:
            raise CrabError("release merge commit is not on the default branch")
        for path in pr["receipt"]["changed_paths"]:
            if pr["receipt"]["version"] not in _blob(git, sha, path):
                raise CrabError("merged release files no longer contain the reviewed version")
        remote = git.run(
            "ls-remote", "--tags", "origin", f"refs/tags/{name}", f"refs/tags/{name}^{{}}"
        ).splitlines()
        if remote:
            target = remote[-1].split()[0]
            if target != sha:
                raise CrabError("release tag already points to a different commit")
        else:
            local = git.try_run("rev-parse", "--verify", f"refs/tags/{name}^{{commit}}")
            if local is not None and local.strip() != sha:
                raise CrabError("local release tag already points to a different commit")
            if local is None:
                git.run("update-ref", f"refs/tags/{name}", sha)
            git.run("-c", "core.hooksPath=", "push", "origin", f"refs/tags/{name}:refs/tags/{name}")
        pr.update(state="merged", merge_sha=sha, tagged=name)
        data["waiting_on"] = None
        loop._save(data)
        return dict(pr)
