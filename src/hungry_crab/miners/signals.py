"""Provider discussion/review metadata and CI reliability statistics, without prose summaries."""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

from ..mdutil import MdDoc
from ..typeutil import as_dict, as_list
from .base import MineContext, MinerResult


class SignalsMiner:
    name = "signals"
    requires = ("inventory",)
    json_file = "signals.json"
    md_file = "signals.md"

    def run(self, ctx: MineContext) -> MinerResult:
        channels = {name: as_dict(ctx.api.get(name)) for name in ("discussions", "reviews", "runs")}
        discussion_items = [as_dict(x) for x in as_list(channels["discussions"].get("items"))]
        review_items = [as_dict(x) for x in as_list(channels["reviews"].get("items"))]
        run_items = [as_dict(x) for x in as_list(channels["runs"].get("items"))]
        paths = {f.path for f in ctx.files()}
        review_paths = Counter(str(x["path"]) for x in review_items if x.get("path") in paths)
        durations = [
            float(x["duration_seconds"])
            for x in run_items
            if isinstance(x.get("duration_seconds"), int | float) and x["duration_seconds"] >= 0
        ]
        recoveries = sum(
            j.get("recovered_on_rerun") is True
            for r in run_items
            for j in map(as_dict, as_list(r.get("jobs")))
        )
        reports = [
            as_dict(report) for run in run_items for report in as_list(run.get("test_reports"))
        ]
        reported_flakes = sum(
            case.get("flaky_rerun") is True
            for report in reports
            for case in map(as_dict, as_list(report.get("tests")))
        )
        data: dict[str, Any] = {
            "channels": channels,
            "discussions": {
                "count": len(discussion_items),
                "answered": sum(x.get("answered") is True for x in discussion_items),
            },
            "reviews": {
                "count": len(review_items),
                "paths": [
                    {"path": p, "comments": n}
                    for p, n in sorted(review_paths.items(), key=lambda x: (-x[1], x[0]))
                ],
            },
            "runs": {
                "count": len(run_items),
                "failures": sum(x.get("conclusion") in {"failure", "failed"} for x in run_items),
                "duration_median_seconds": statistics.median(durations) if durations else None,
                "job_rerun_recoveries": recoveries,
                "flaky_test_count": reported_flakes
                if any(r.get("reports") for r in reports)
                else None,
                "flaky_test_evidence": "explicit JUnit flakyFailure/flakyError markers only",
                "report_coverage": dict(
                    sorted(Counter(str(r.get("status")) for r in reports).items())
                ),
            },
        }
        doc = MdDoc(f"Provider signals: {ctx.label}", source=ctx.source_line())
        section = doc.section("Acquisition coverage", priority=1)
        section.table(
            ["Channel", "Status", "Truncated"],
            (
                [name, raw.get("status", "not-requested"), raw.get("truncated", False)]
                for name, raw in channels.items()
            ),
        )
        doc.section("Discussions and reviews", priority=2).kv(
            [
                (
                    "Discussions / answered",
                    f"{len(discussion_items)} / {data['discussions']['answered']}",
                ),
                ("Review comments", len(review_items)),
            ]
        )
        doc.section("Reviewed paths", priority=2).table(
            ["Path", "Comments"], ([x["path"], x["comments"]] for x in data["reviews"]["paths"])
        )
        ci = doc.section("CI run sample", priority=2)
        ci.kv([(k, v) for k, v in data["runs"].items() if k != "flaky_test_evidence"])
        ci.para(
            "A job recovering on a rerun is a reliability signal, not proof of a flaky test. "
            "GitHub durations sum job seconds; parallel execution is not wall time. "
            "Discussion titles and review prose stay out of Markdown."
        )
        warnings = [
            f"{name} acquisition was truncated"
            for name, raw in channels.items()
            if raw.get("truncated")
        ]
        return MinerResult(self.name, data, doc=doc, warnings=warnings)
