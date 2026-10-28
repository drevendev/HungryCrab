from __future__ import annotations

import copy
import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from hungry_crab.cache import Slug, Target, prey_paths
from hungry_crab.cli import main
from hungry_crab.errors import CrabError, UsageError
from hungry_crab.feeder import EatOptions, EatResult
from hungry_crab.multifeed import eat_many, load_multi, merge_menus
from hungry_crab.nutrients import Candidate


@contextmanager
def fake_acquisition_scope(*_args: object) -> Iterator[None]:
    """Skip the remote quota guard when a test replaces acquisition with a fixture."""
    yield None


def menu(prey: str, mode: str = "COPY", *, sha: str = "a", score: float = 0.7) -> dict[str, Any]:
    card = Candidate(
        "ci",
        "ci.cache",
        "Cache CI",
        "x",
        license_mode=mode,
        origin="licensed",
        score=score,
        trace={"prey": prey, "sha": sha},
    )
    return {
        "schema": "hungry-crab.menu/1",
        "generated_at": "2026-10-08T12:00:00+00:00",
        "mode": "normal",
        "maw": {"label": "maw", "sha": "m", "root": "/maw"},
        "prey": {"label": prey, "sha": sha, "url": None, "license": "MIT"},
        "candidates": [card.to_dict()],
        "hidden": [],
        "counts": {"total": 1, "top": 30},
    }


def test_dedup_keeps_every_pin_and_uses_strictest_source_without_score_boost() -> None:
    sources = {
        "copy": menu("x/copy", score=0.9),
        "restricted": menu("x/gpl", "REIMPLEMENT", sha="b", score=0.4),
    }
    before = copy.deepcopy(sources)
    result = merge_menus(sources)
    assert sources == before
    assert result["counts"]["total"] == 1
    card = result["candidates"][0]
    assert card["license_mode"] == "REIMPLEMENT" and card["score"] == 0.4
    assert card["trace"]["primary_source"] == "restricted"
    assert {source["prey"]["sha"] for source in card["trace"]["sources"]} == {"a", "b"}
    assert merge_menus(dict(reversed(list(sources.items())))) == result


def test_maw_relative_dedup_preserves_prey_specific_lessons_and_hidden_sources() -> None:
    sources = {"a": menu("x/a"), "b": menu("x/b")}
    for name, source in sources.items():
        card = Candidate("history-lesson", f"history-lesson.{name}.fix-prone", "Fix-prone", "x")
        source["candidates"].append(card.to_dict())
        source["hidden"] = [{"id": "hidden", "reason": "ledger: rejected"}]
    result = merge_menus(sources)
    assert result["counts"]["total"] == 3 and result["counts"]["hidden"] == 2
    sources["b"]["maw"]["sha"] = "another"
    with pytest.raises(CrabError, match="snapshot"):
        merge_menus(sources)


def fake_eat(prey: Target, maw: Path, options: EatOptions, **_: Any) -> EatResult:
    assert options.out is not None
    options.out.mkdir()
    data = menu(prey.label, sha="a" * 40)
    if prey.slug is not None:
        data["prey"]["url"] = prey.slug.url
        prey_root = prey_paths(prey.slug, options.cache_root).repo
    else:
        assert prey.path is not None
        prey_root = prey.path
    data["maw"]["root"] = str(maw.resolve())
    (options.out / "menu.json").write_text(json.dumps(data), encoding="utf-8")
    digest = options.out / "prey-digest"
    digest.mkdir()
    (digest / "manifest.json").write_text(
        json.dumps({"prey": {"root": str(prey_root)}}), encoding="utf-8"
    )
    return EatResult(options.out, {"prey": data["prey"]})


def legacy_v1_bundle(root: Path, sources: list[tuple[str, dict[str, Any], str]]) -> dict[str, Any]:
    """Write the version 1 on-disk shape directly, as the pre-v2 writer did."""
    from hungry_crab.multifeed import _fingerprints

    root.mkdir(parents=True)
    (root / "sources").mkdir()
    source_menus: dict[str, dict[str, Any]] = {}
    rows = []
    for target, source_menu, digest_root in sorted(sources, key=lambda row: row[0]):
        source_id = hashlib.sha256(target.encode("utf-8")).hexdigest()[:20]
        source = root / "sources" / source_id
        (source / "prey-digest").mkdir(parents=True)
        (source / "menu.json").write_text(json.dumps(source_menu), encoding="utf-8")
        (source / "prey-digest" / "manifest.json").write_text(
            json.dumps({"prey": {"root": digest_root}}), encoding="utf-8"
        )
        rows.append(
            {
                "id": source_id,
                "path": f"sources/{source_id}",
                "target": target,
                "prey": source_menu["prey"],
                "files": _fingerprints(source),
            }
        )
        source_menus[source_id] = source_menu

    aggregate = merge_menus(source_menus)
    (root / "menu.json").write_text(
        json.dumps(aggregate, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (root / "feeder.json").write_text(
        json.dumps(
            {
                "schema": "hungry-crab.feeder-multi/1",
                "maw": aggregate["maw"],
                "counts": aggregate["counts"],
                "sources": rows,
                "side_effects": {"issues_created": 0, "ledger_written": False},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return aggregate


def test_atomic_bundle_integrity_and_cli_menu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    maw = tmp_path / "maw"
    maw.mkdir()
    monkeypatch.setattr("hungry_crab.multifeed.eat", fake_eat)
    result = eat_many(
        [Target(slug=None, path=maw.parent / "a"), Target(path=maw.parent / "b")],
        maw,
        EatOptions(out=tmp_path / "bundle", cache_root=tmp_path / "cache"),
    )
    merged, paths, _ = load_multi(result.out_dir)
    assert len(paths) == 2 and merged["counts"]["total"] == 1
    assert result.manifest["side_effects"] == {"issues_created": 0, "ledger_written": False}
    assert not (maw / ".crab").exists()
    assert main(["menu", "--meal-dir", str(result.out_dir), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "multi-prey"
    aggregate_path = result.out_dir / "menu.json"
    original = aggregate_path.read_text()
    altered = json.loads(original)
    altered["candidates"][0]["license_mode"] = "HUMAN"
    aggregate_path.write_text(json.dumps(altered), encoding="utf-8")
    with pytest.raises(CrabError, match="differs"):
        load_multi(result.out_dir)
    aggregate_path.write_text(original, encoding="utf-8")
    (next(iter(paths.values())) / "menu.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CrabError, match="integrity"):
        load_multi(result.out_dir)


def test_multi_bundle_preserves_slug_case_and_hashes_normalized_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    maw = tmp_path / "maw"
    maw.mkdir()
    monkeypatch.setattr("hungry_crab.multifeed.eat", fake_eat)
    monkeypatch.setattr("hungry_crab.multifeed.acquisition_scope", fake_acquisition_scope)
    result = eat_many(
        [
            Target(slug=Slug.parse("Owner/MixedRepo")),
            Target(slug=Slug.parse("Other/SecondRepo")),
        ],
        maw,
        EatOptions(out=tmp_path / "bundle", cache_root=tmp_path / "cache"),
    )

    manifest = json.loads((result.out_dir / "feeder.json").read_text(encoding="utf-8"))
    source = next(row for row in manifest["sources"] if row["target"]["value"] == "Owner/MixedRepo")
    identity = "owner/mixedrepo"
    assert source["identity"] == identity
    assert source["id"] == hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    assert source["target"] == {"kind": "slug", "value": "Owner/MixedRepo"}

    _menu, _paths, targets = load_multi(result.out_dir)
    assert targets[source["id"]] == Target(slug=Slug.parse("Owner/MixedRepo"))


def test_multi_bundle_rejects_case_only_slug_retargeting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    maw = tmp_path / "maw"
    maw.mkdir()
    monkeypatch.setattr("hungry_crab.multifeed.eat", fake_eat)
    monkeypatch.setattr("hungry_crab.multifeed.acquisition_scope", fake_acquisition_scope)
    result = eat_many(
        [
            Target(slug=Slug.parse("Owner/MixedRepo")),
            Target(slug=Slug.parse("Other/SecondRepo")),
        ],
        maw,
        EatOptions(out=tmp_path / "bundle", cache_root=tmp_path / "cache"),
    )
    manifest_path = result.out_dir / "feeder.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source = next(row for row in manifest["sources"] if row["target"]["value"] == "Owner/MixedRepo")
    source["target"]["value"] = "owner/mixedrepo"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(CrabError, match="does not match source menu"):
        load_multi(result.out_dir)


def test_multi_bundle_rejects_source_id_that_does_not_hash_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    maw = tmp_path / "maw"
    maw.mkdir()
    monkeypatch.setattr("hungry_crab.multifeed.eat", fake_eat)
    monkeypatch.setattr("hungry_crab.multifeed.acquisition_scope", fake_acquisition_scope)
    result = eat_many(
        [
            Target(slug=Slug.parse("Owner/MixedRepo")),
            Target(slug=Slug.parse("Other/SecondRepo")),
        ],
        maw,
        EatOptions(out=tmp_path / "bundle", cache_root=tmp_path / "cache"),
    )
    manifest_path = result.out_dir / "feeder.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"][0]["identity"] = "other/identity"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(CrabError, match="source id does not match identity"):
        load_multi(result.out_dir)


@pytest.mark.parametrize("pre_context_aggregate", [False, True])
def test_multi_bundle_v1_preserves_receiving_context_and_recovers_slug_case(
    tmp_path: Path,
    pre_context_aggregate: bool,
) -> None:
    context = {
        "sha": "m" * 40,
        "selected_ref": "main",
        "root": "/maw",
        "worktree": "/maw",
    }
    slugs = [Slug.parse("Owner/MixedRepo"), Slug.parse("Other/SecondRepo")]
    sources = []
    for slug in slugs:
        source_menu = menu(str(slug))
        source_menu["prey"]["url"] = slug.url
        source_menu["receiving_context"] = context
        source_menu["analysis_inputs"] = {
            "policy": {"mode": "normal"},
            "ledger": {"entries": []},
        }
        sources.append(
            (
                str(slug).casefold(),
                source_menu,
                str(prey_paths(slug, tmp_path / "cache").repo),
            )
        )

    bundle = tmp_path / "legacy-bundle"
    saved = legacy_v1_bundle(bundle, sources)
    assert saved["receiving_context"] == context
    assert saved["receiving_context_status"] == "observed"
    if pre_context_aggregate:
        saved.pop("receiving_context")
        saved.pop("receiving_context_status")
        (bundle / "menu.json").write_text(json.dumps(saved), encoding="utf-8")

    merged, _paths, targets = load_multi(bundle)
    if pre_context_aggregate:
        assert "receiving_context" not in merged
        assert "receiving_context_status" not in merged
    else:
        assert merged["receiving_context"] == context
        assert merged["receiving_context_status"] == "observed"
    for slug in slugs:
        source_id = hashlib.sha256(str(slug).casefold().encode("utf-8")).hexdigest()[:20]
        assert targets[source_id] == Target(slug=slug)


def test_multi_bundle_v1_relative_local_target_fails_closed_after_cwd_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    producer = tmp_path / "producer"
    (producer / "project").mkdir(parents=True)
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    monkeypatch.chdir(producer)

    relative_target = Path("project")
    local_identity = os.path.normcase(str(relative_target.resolve()))
    local_menu = menu("project")
    context = {"sha": "m" * 40, "selected_ref": "main", "root": "/maw", "worktree": "/maw"}
    local_menu["receiving_context"] = context
    local_menu["analysis_inputs"] = {
        "policy": {"mode": "normal"},
        "ledger": {"entries": []},
    }

    remote_slug = Slug.parse("Other/RemoteRepo")
    remote_menu = menu(str(remote_slug))
    remote_menu["prey"]["url"] = remote_slug.url
    remote_menu["receiving_context"] = context
    remote_menu["analysis_inputs"] = {
        "policy": {"mode": "normal"},
        "ledger": {"entries": []},
    }
    bundle = tmp_path / "legacy-relative-bundle"
    legacy_v1_bundle(
        bundle,
        [
            (local_identity, local_menu, str(relative_target)),
            (
                str(remote_slug).casefold(),
                remote_menu,
                str(prey_paths(remote_slug, tmp_path / "cache").repo),
            ),
        ],
    )

    # The old writer stored an absolute target identity but a relative digest root.
    local_id = hashlib.sha256(local_identity.encode("utf-8")).hexdigest()[:20]
    assert load_multi(bundle)[2][local_id] == Target(path=Path(local_identity))
    monkeypatch.chdir(consumer)
    with pytest.raises(CrabError, match="legacy local target does not match acquired source path"):
        load_multi(bundle)


def test_colliding_local_prey_labels_are_rejected_before_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    maw = tmp_path / "maw"
    maw.mkdir()
    first = tmp_path / "x" / "app"
    second = tmp_path / "y" / "app"
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    def forbidden_eat(*_args: Any, **_kwargs: Any) -> EatResult:
        pytest.fail("prey with ambiguous nutrient ids must not be acquired")

    monkeypatch.setattr("hungry_crab.multifeed.eat", forbidden_eat)
    out = tmp_path / "bundle"
    with pytest.raises(UsageError, match="ambiguous prey labels"):
        eat_many(
            [Target(path=first), Target(path=second)],
            maw,
            EatOptions(out=out, cache_root=tmp_path / "cache"),
        )
    assert not out.exists()
    assert not list(tmp_path.glob(".bundle-*"))


def test_distinct_remote_repositories_with_colliding_slugs_are_refused(tmp_path: Path) -> None:
    maw = tmp_path / "maw"
    maw.mkdir()
    with pytest.raises(UsageError, match="ambiguous prey labels"):
        eat_many(
            [Target(slug=Slug.parse("a/b-c")), Target(slug=Slug.parse("a-b/c"))],
            maw,
            EatOptions(out=tmp_path / "bundle", cache_root=tmp_path / "cache"),
        )
    assert not (tmp_path / "bundle").exists()


def test_existing_multi_menus_with_ambiguous_labels_fail_closed() -> None:
    with pytest.raises(CrabError, match="ambiguous prey labels"):
        merge_menus({"first": menu("a/b-c"), "second": menu("a-b/c")})


def test_duplicate_or_failed_sources_never_publish_partial_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    maw = tmp_path / "maw"
    maw.mkdir()
    options = EatOptions(out=tmp_path / "bundle", cache_root=tmp_path / "cache")
    target = Target(path=tmp_path / "prey")
    with pytest.raises(UsageError, match="duplicate"):
        eat_many([target, target], maw, options)
    calls = 0

    def fail_second(prey: Target, maw: Path, options: EatOptions, **kwargs: Any) -> EatResult:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise CrabError("acquisition failed")
        return fake_eat(prey, maw, options)

    monkeypatch.setattr("hungry_crab.multifeed.eat", fail_second)
    with pytest.raises(CrabError, match="acquisition failed"):
        eat_many([target, Target(path=tmp_path / "other")], maw, options)
    assert not options.out.exists()  # type: ignore[union-attr]
    assert not list(tmp_path.glob(".bundle-*"))


def test_real_multi_feeder_retains_verified_digests_and_dry_run_serving(
    npm_app: Path,
    pyproject_cli: Path,
    dotnet_lib: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "bundle"
    cache = tmp_path / "cache"
    args = [
        "--cache-dir",
        str(cache),
        "eat",
        str(npm_app),
        str(dotnet_lib),
        "--deterministic",
        "--maw",
        str(pyproject_cli),
        "--out",
        str(out),
        "--no-wiki",
        "--json",
    ]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == "hungry-crab.feeder-multi/2"
    merged, paths, _ = load_multi(out)
    assert len(paths) == 2 and merged["candidates"]
    for path in paths.values():
        assert (path / "prey-digest" / "manifest.json").is_file()
    assert (
        main(
            [
                "--cache-dir",
                str(cache),
                "serve",
                "--meal-dir",
                str(out),
                "--maw",
                str(pyproject_cli),
                "--top",
                "1",
                "--json",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "dry-run" and not report["served"]
    assert report["previews"] or report["skipped"]
