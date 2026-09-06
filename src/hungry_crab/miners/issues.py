"""Issues miner: what users of the prey keep asking for and complaining about.

Reads the JSONL that ``crab catch --issues N`` stored. Statistics, the most reacted-to issues and
TF-IDF clusters go to ``issues.json``/``issues.md``; issue text itself is ``IDEAS_ONLY`` and never
reaches the summary beyond sanitized titles.
"""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from datetime import datetime
from typing import Any

from ..mdutil import MdDoc
from ..safety import is_suspicious
from ..typeutil import as_dict, as_list
from .base import MineContext, MinerResult

_TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{2,}")
STOPWORDS = frozenset(
    """a about above after again all also an and any are as at be because been before being
    below between both but by can could did do does doing down during each few for from further
    had has have having he her here hers him his how i if in into is it its itself just let me
    more most my no nor not now of off on once only or other our out over own same she should
    so some such than that the their them then there these they this those through to too under
    until up use used using very was we were what when where which while who whom why will with
    would you your yours issue issues error errors bug bugs feature request please thanks thank
    like want would work works working add support added using version versions get got set
    make made new one two also still even see seems seem code file files way""".split()  # noqa: SIM905
)
CLUSTER_THRESHOLD = 0.2
MIN_CLUSTER = 3


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _title(text: object) -> str:
    title = str(text or "").strip()
    if is_suspicious(title):
        return "[title omitted: instruction-like]"
    return title[:90]


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in STOPWORDS]


def _normalized(vector: dict[str, float]) -> dict[str, float]:
    norm = math.sqrt(sum(w * w for w in vector.values())) or 1.0
    return {term: w / norm for term, w in vector.items()}


def cluster_issues(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Centroid clustering over TF-IDF vectors: cheap, deterministic, good enough for a summary.

    Issues are visited by reactions (most popular first) and join the first cluster whose
    centroid is similar enough; otherwise they start a new one.
    """
    docs = []
    for item in items:
        tokens = tokenize(f"{item.get('title', '')} {item.get('body_excerpt', '')}")
        if tokens:
            docs.append((item, Counter(tokens)))
    if len(docs) < MIN_CLUSTER:
        return []
    df: Counter[str] = Counter()
    for _, counts in docs:
        df.update(counts.keys())
    total = len(docs)
    vectors: list[tuple[dict[str, Any], dict[str, float]]] = []
    for item, counts in docs:
        weights = {
            term: (1 + math.log(count)) * math.log(total / df[term])
            for term, count in counts.items()
            if df[term] < total
        }
        vectors.append((item, _normalized(weights)))
    vectors.sort(
        key=lambda pair: (-int(pair[0].get("reactions") or 0), -int(pair[0].get("number") or 0))
    )
    clusters: list[dict[str, Any]] = []
    for item, vector in vectors:
        best: dict[str, Any] | None = None
        best_score = 0.0
        for cluster in clusters:
            centroid = cluster["_centroid"]
            score = sum(w * centroid.get(term, 0.0) for term, w in vector.items())
            if score > best_score:
                best, best_score = cluster, score
        if best is not None and best_score >= CLUSTER_THRESHOLD:
            best["_members"].append(item)
            for term, w in vector.items():
                best["_sum"][term] = best["_sum"].get(term, 0.0) + w
            best["_centroid"] = _normalized(best["_sum"])
        else:
            clusters.append({"_members": [item], "_sum": dict(vector), "_centroid": dict(vector)})
    out: list[dict[str, Any]] = []
    for cluster in clusters:
        members = cluster["_members"]
        if len(members) < MIN_CLUSTER:
            continue
        terms = sorted(cluster["_sum"].items(), key=lambda kv: -kv[1])[:5]
        labels: Counter[str] = Counter(
            str(label) for m in members for label in as_list(m.get("labels"))
        )
        out.append(
            {
                "size": len(members),
                "terms": [term for term, _ in terms],
                "open": sum(1 for m in members if m.get("state") == "open"),
                "reactions": sum(int(m.get("reactions") or 0) for m in members),
                "top_label": labels.most_common(1)[0][0] if labels else None,
                "numbers": [m.get("number") for m in members[:20]],
                "sample_titles": [_title(m.get("title")) for m in members[:3]],
            }
        )
    out.sort(key=lambda c: (-c["size"], -c["reactions"]))
    return out[:15]


def _time_to_close(closed: list[dict[str, Any]]) -> dict[str, Any]:
    durations = []
    for item in closed:
        created, closed_at = _parse(item.get("created_at")), _parse(item.get("closed_at"))
        if created and closed_at and closed_at >= created:
            durations.append((closed_at - created).total_seconds() / 86400)
    ordered = sorted(durations)
    return {
        "median": round(statistics.median(ordered), 1) if ordered else None,
        "p90": round(ordered[int(len(ordered) * 0.9) - 1], 1) if len(ordered) >= 10 else None,
        "samples": len(ordered),
    }


class IssuesMiner:
    name = "issues"
    requires: tuple[str, ...] = ("inventory",)
    json_file = "issues.json"
    md_file = "issues.md"

    def run(self, ctx: MineContext) -> MinerResult:
        items = [as_dict(item) for item in as_list(ctx.api.get("issues"))]
        if not items:
            reason = "no issues fetched; run `crab catch <prey> --issues 300`"
            data: dict[str, Any] = {"available": False, "reason": reason}
            doc = MdDoc(f"Issues: {ctx.label}", source=ctx.source_line())
            doc.section("Summary", priority=1).para(f"Issues not available: {reason}.")
            return MinerResult(self.name, data, doc=doc)
        open_items = [i for i in items if i.get("state") == "open"]
        closed = [i for i in items if i.get("state") == "closed"]
        labels: Counter[str] = Counter(
            str(label) for i in items for label in as_list(i.get("labels"))
        )
        comments = [int(i.get("comments") or 0) for i in items]
        by_reactions = sorted(
            items, key=lambda i: (-int(i.get("reactions") or 0), -int(i.get("number") or 0))
        )
        oldest_open = sorted(
            (i for i in open_items if _parse(i.get("created_at"))),
            key=lambda i: str(i.get("created_at")),
        )
        data = {
            "available": True,
            "fetched": len(items),
            "open": len(open_items),
            "closed": len(closed),
            "labels": [{"name": name, "count": count} for name, count in labels.most_common(15)],
            "unlabeled": sum(1 for i in items if not as_list(i.get("labels"))),
            "time_to_close_days": _time_to_close(closed),
            "comments": {
                "median": statistics.median(comments) if comments else 0,
                "max": max(comments) if comments else 0,
                "no_comments": sum(1 for c in comments if c == 0),
            },
            "top_by_reactions": [
                {
                    "number": i.get("number"),
                    "title": _title(i.get("title")),
                    "reactions": int(i.get("reactions") or 0),
                    "comments": int(i.get("comments") or 0),
                    "state": i.get("state"),
                    "labels": [str(label) for label in as_list(i.get("labels"))][:5],
                    "url": i.get("url"),
                }
                for i in by_reactions[:10]
                if int(i.get("reactions") or 0) > 0
            ],
            "oldest_open": [
                {
                    "number": i.get("number"),
                    "title": _title(i.get("title")),
                    "created_at": i.get("created_at"),
                    "url": i.get("url"),
                }
                for i in oldest_open[:5]
            ],
            "clusters": cluster_issues(items),
            "suspicious_titles": sum(1 for i in items if is_suspicious(str(i.get("title") or ""))),
        }
        return MinerResult(self.name, data, doc=self._doc(ctx, data))

    def _doc(self, ctx: MineContext, data: dict[str, Any]) -> MdDoc:
        doc = MdDoc(f"Issues: {ctx.label}", source=ctx.source_line())
        summary = doc.section("Summary", priority=1)
        ttc = data["time_to_close_days"]
        comments = data["comments"]
        close_text = (
            f"{ttc['median']} days over {ttc['samples']} closed issues"
            if ttc["median"] is not None
            else "n/a"
        )
        comment_text = (
            f"median {comments['median']}, max {comments['max']}, "
            f"{comments['no_comments']} without any"
        )
        summary.kv(
            [
                (
                    "Issues fetched",
                    f"{data['fetched']} ({data['open']} open, {data['closed']} closed)",
                ),
                ("Median time to close", close_text),
                ("Comments", comment_text),
                ("Unlabeled", data["unlabeled"]),
                ("Clusters (3+ issues)", len(data["clusters"])),
            ]
        )
        if data["labels"]:
            labels = doc.section("Labels", priority=3)
            labels.table(
                ["Label", "Issues"], ([row["name"], row["count"]] for row in data["labels"])
            )
        if data["top_by_reactions"]:
            top = doc.section("Most reacted-to issues", priority=1)
            top.table(
                ["#", "Reactions", "Comments", "State", "Title"],
                (
                    [t["number"], t["reactions"], t["comments"], t["state"], t["title"]]
                    for t in data["top_by_reactions"]
                ),
            )
        if data["clusters"]:
            clusters = doc.section("Recurring themes (TF-IDF clusters)", priority=2)
            for cluster in data["clusters"]:
                numbers = ", ".join(f"#{n}" for n in cluster["numbers"][:8])
                label = f", label {cluster['top_label']}" if cluster["top_label"] else ""
                clusters.line(
                    f"- **{', '.join(cluster['terms'])}**: {cluster['size']} issues "
                    f"({cluster['open']} open, {cluster['reactions']} reactions{label}): {numbers}"
                )
            clusters.line("")
        if data["oldest_open"]:
            oldest = doc.section("Oldest open issues", priority=4)
            oldest.table(
                ["#", "Opened", "Title"],
                ([o["number"], str(o["created_at"])[:10], o["title"]] for o in data["oldest_open"]),
            )
        return doc
