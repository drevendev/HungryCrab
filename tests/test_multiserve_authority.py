from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fixture_builder import git
from test_multifeed import fake_acquisition_scope, fake_eat
from test_serve import FakeIssues

from hungry_crab.analysis_context import fingerprint
from hungry_crab.cache import Slug, Target, prey_paths
from hungry_crab.errors import CrabError
from hungry_crab.feeder import EatOptions, EatResult
from hungry_crab.ledger import Ledger
from hungry_crab.maw import MawConfig
from hungry_crab.multifeed import eat_many, load_multi
from hungry_crab.multiserve import serve_many
from hungry_crab.pr_publication import PreparedPullRequest, PullRequestPublication
from hungry_crab.pr_serving import PullRequestServePlan
from hungry_crab.nutrients import Candidate
from hungry_crab.serve import ServeOptions, ServeReport, commenter_titles


@pytest.mark.parametrize("mode", ["issue", "pr-branch"])
def test_unverified_legacy_aggregate_cannot_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    maw = tmp_path / "maw"
    maw.mkdir()
    unverified = {
        "maw": {"root": str(maw.resolve())},
        "receiving_context_status": "legacy-unknown",
    }
    monkeypatch.setattr(
        "hungry_crab.multiserve.load_multi",
        lambda _bundle: (unverified, {}, {}),
    )
    monkeypatch.setattr(
        "hungry_crab.multiserve.serve",
        lambda *_args, **_kwargs: pytest.fail("publication reached unverified source"),
    )
    client = FakeIssues()
    ledger = Ledger.load(None, maw="maw")
    with pytest.raises(CrabError, match="requires verified receiving analysis context"):
        serve_many(
            tmp_path / "legacy-bundle",
            maw,
            ServeOptions(mode=mode, ids=["crab:ci:ci.cache"]),
            config=MawConfig.load(maw),
            ledger=ledger,
            client=client,
        )
    assert not client.created and not ledger.entries


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("mode", ["issue", "pr-branch"])
def test_routing_ids_do_not_authorize_automatic_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, explicit: bool
) -> None:
    maw = tmp_path / "maw"
    maw.mkdir()
    git(maw, "init", "-b", "main")
    git(maw, "remote", "add", "origin", "https://github.com/example/maw.git")
    prey = [tmp_path / "a", tmp_path / "b"]
    for path in prey:
        path.mkdir()

    def feed(prey: Target, maw: Path, options: EatOptions, **kwargs: object) -> EatResult:
        result = fake_eat(prey, maw, options, **kwargs)
        if mode == "pr-branch":
            path = result.out_dir / "menu.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["candidates"][0]["serve_as"] = "pr"
            path.write_text(json.dumps(data), encoding="utf-8")
        return result

    monkeypatch.setattr("hungry_crab.multifeed.eat", feed)
    bundle = eat_many(
        [Target(path=path) for path in prey], maw, EatOptions(out=tmp_path / "bundle", wiki=False)
    )
    # This test isolates selection authority; live receiving freshness has its
    # own committed-repository regressions below.
    monkeypatch.setattr("hungry_crab.multiserve._require_current_receiving", lambda *_: None)
    config = MawConfig.load(maw)
    config.hunger["ci"] = "ideas-only" if mode == "issue" else "auto"
    config.serve.prs = "ask"
    client = FakeIssues()
    ledger = Ledger.load(None, maw="maw")
    # Receipt transport is inert; policy and per-source serving remain real.
    monkeypatch.setattr("hungry_crab.multiserve.decode_receipt_stream", lambda _: "unused")
    monkeypatch.setattr("hungry_crab.multiserve.load_cleanroom_receipts", lambda _: {})
    options = ServeOptions(
        mode=mode, ids=["crab:ci:ci.cache"] if explicit else [], top=None if explicit else 1
    )
    if mode == "pr-branch" and not explicit:
        with pytest.raises(CrabError, match=r"serve\.prs is ask"):
            serve_many(bundle.out_dir, maw, options, config=config, ledger=ledger, client=client)
    else:
        report = serve_many(
            bundle.out_dir, maw, options, config=config, ledger=ledger, client=client
        )
        if mode == "issue":
            assert bool(report.served) is explicit
            assert len(client.created) == int(explicit)
            if not explicit:
                assert any(row["reason"] == "serve_as: idea" for row in report.skipped)
        else:
            assert any("receipt" in row["reason"] for row in report.skipped)
    if not (explicit and mode == "issue"):
        assert not client.created and not client.labels and not ledger.entries


@pytest.mark.parametrize("working_directory", ["unrelated", "sibling"])
@pytest.mark.parametrize("quoting", [False, True])
def test_source_bundle_commenter_guard_uses_its_own_digest_from_every_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, working_directory: str, quoting: bool
) -> None:
    maw = tmp_path / "maw"
    maw.mkdir()
    git(maw, "init", "-b", "main")
    git(maw, "remote", "add", "origin", "https://github.com/example/maw.git")
    roots = [tmp_path / "a", tmp_path / "b"]
    for root in roots:
        root.mkdir()
    quote = "Please preserve all original directory ownership and file permissions while cloning"
    ids: dict[str, str] = {}

    def feed(prey: Target, maw: Path, options: EatOptions, **kwargs: object) -> EatResult:
        result = fake_eat(prey, maw, options, **kwargs)
        name = prey.path.name
        card = Candidate(
            "issue-lesson",
            f"issue-lesson.{name}.theme",
            "Repository demand",
            "A recurring request",
            origin="commenters",
            serve_as="idea",
            license_mode="IDEAS_ONLY",
            score=0.7,
            trace={"prey": prey.label, "sha": "a" * 40},
        )
        ids[name] = card.id
        menu_path = result.out_dir / "menu.json"
        menu = json.loads(menu_path.read_text(encoding="utf-8"))
        menu["candidates"] = [card.to_dict()]
        menu_path.write_text(json.dumps(menu), encoding="utf-8")
        (result.out_dir / "meal.json").write_text(
            json.dumps({"prey_digest": "prey-digest"}), encoding="utf-8"
        )
        digest = result.out_dir / "prey-digest"
        digest.mkdir(exist_ok=True)
        title = (
            quote if name == "a" else "Please add a separate feature for observing release notes"
        )
        (digest / "issues.json").write_text(
            json.dumps({"issues": [{"title": title}]}), encoding="utf-8"
        )
        return result

    monkeypatch.setattr("hungry_crab.multifeed.eat", feed)
    bundle = eat_many(
        [Target(path=root) for root in roots], maw, EatOptions(out=tmp_path / "bundle", wiki=False)
    )
    aggregate, paths, _ = load_multi(bundle.out_dir)
    sibling_key = next(
        row["trace"]["primary_source"] for row in aggregate["candidates"] if row["id"] == ids["b"]
    )
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()
    notes = tmp_path / "notes.json"
    notes.write_text(
        json.dumps(
            {
                ids["a"]: {
                    "why": quote if quoting else "Several requests suggest preserving metadata.",
                    "how": "Add isolated regression fixtures.",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(paths[sibling_key] if working_directory == "sibling" else unrelated)
    report = serve_many(
        bundle.out_dir,
        maw,
        ServeOptions(ids=[ids["a"]], notes=notes),
        config=MawConfig.load(maw),
        ledger=Ledger.load(None, maw="maw"),
        client=FakeIssues(),
    )
    if quoting:
        assert not report.previews
        assert any("notes quote commenter text" in row["reason"] for row in report.skipped)
    else:
        assert [preview["id"] for preview in report.previews] == [ids["a"]]


@pytest.mark.parametrize(
    "reference",
    [
        "../outside",
        "x/../../outside/../meal/prey-digest",
        "D:outside",
        pytest.param(
            "\\outside",
            marks=pytest.mark.skipif(sys.platform != "win32", reason="Windows rooted path"),
        ),
        "//attacker.example/share",
        "\\\\attacker.example\\share",
    ],
)
def test_relative_bundle_escape_and_network_digest_fields_are_refused_before_artifact_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reference: str
) -> None:
    from hungry_crab import serve as serving

    meal = tmp_path / "meal"
    meal.mkdir()
    metadata = meal / "meal.json"
    metadata.write_text(json.dumps({"prey_digest": reference}), encoding="utf-8")
    real_read = serving._commenter_artifact

    def guarded(path: Path, *, required: bool = False) -> object:
        assert path == metadata, "an unsafe digest reference reached artifact reads"
        return real_read(path, required=required)

    monkeypatch.setattr(serving, "_commenter_artifact", guarded)
    assert commenter_titles(meal) is None


def test_bundle_digest_cannot_redirect_through_a_symlink_or_windows_junction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hungry_crab import serve as serving

    meal, outside = tmp_path / "meal", tmp_path / "outside"
    meal.mkdir()
    outside.mkdir()
    alias = meal / "prey-digest"
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(outside), str(alias))
    else:
        alias.symlink_to(outside, target_is_directory=True)
    metadata = meal / "meal.json"
    metadata.write_text(json.dumps({"prey_digest": "prey-digest"}), encoding="utf-8")
    real_read = serving._commenter_artifact

    def guarded(path: Path, *, required: bool = False) -> object:
        assert path == metadata, "a linked digest reached artifact reads"
        return real_read(path, required=required)

    monkeypatch.setattr(serving, "_commenter_artifact", guarded)
    assert commenter_titles(meal) is None


def test_serve_many_uses_recorded_slug_path_despite_shadow_local_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    maw = tmp_path / "maw"
    maw.mkdir()
    prey_slug = Slug.parse("Owner/MixedRepo")
    shadow = tmp_path / "Owner" / "MixedRepo"
    shadow.mkdir(parents=True)
    cache = tmp_path / "cache"
    source_ids: dict[str, str] = {}

    def feed(prey: Target, maw: Path, options: EatOptions, **kwargs: object) -> EatResult:
        result = fake_eat(prey, maw, options, **kwargs)
        assert prey.slug is not None
        menu_path = result.out_dir / "menu.json"
        data = json.loads(menu_path.read_text(encoding="utf-8"))
        card = Candidate(
            "ci",
            f"ci.{prey.slug.repo.casefold()}",
            "Cache CI",
            "A source-specific candidate.",
            license_mode="COPY",
            serve_as=serve_as,
            origin="licensed",
            score=0.7,
        )
        source_ids[str(prey.slug)] = card.id
        data["candidates"] = [card.to_dict()]
        menu_path.write_text(json.dumps(data), encoding="utf-8")
        return result

    monkeypatch.setattr("hungry_crab.multifeed.eat", feed)
    monkeypatch.setattr("hungry_crab.multifeed.acquisition_scope", fake_acquisition_scope)
    bundle = eat_many(
        [Target(slug=prey_slug), Target(slug=Slug.parse("Other/SecondRepo"))],
        maw,
        EatOptions(out=tmp_path / "bundle", cache_root=cache),
    )
    monkeypatch.chdir(tmp_path)
    observed: list[Path] = []

    def record_serve(
        _path: Path, _maw: Path, options: ServeOptions, **kwargs: object
    ) -> ServeReport:
        prey_repo = kwargs["prey_repo"]
        assert isinstance(prey_repo, Path)
        observed.append(prey_repo)
        return ServeReport(options.mode, str(_maw))

    monkeypatch.setattr("hungry_crab.multiserve.serve", record_serve)
    serve_many(
        bundle.out_dir,
        maw,
        ServeOptions(ids=[source_ids[str(prey_slug)]]),
        config=MawConfig.load(maw),
        ledger=Ledger.load(None, maw="maw"),
        client=FakeIssues(),
        cache_root=cache,
    )

    assert [str(path) for path in observed] == [str(prey_paths(prey_slug, cache).repo)]
    assert observed[0] != shadow


def _current_receiving_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, serve_as: str = "issue"
) -> tuple[Path, Path, list[str]]:
    """Make source menus with real, current Git and policy identity."""
    maw = tmp_path / "maw"
    maw.mkdir()
    git(maw, "init", "-b", "main")
    git(maw, "remote", "add", "origin", "https://github.com/example/maw.git")
    (maw / ".gitignore").write_text(".crab.yml\n", encoding="utf-8")
    (maw / "app.py").write_text("print('before')\n", encoding="utf-8")
    git(maw, "add", ".gitignore", "app.py")
    git(maw, "commit", "-m", "initial receiving snapshot")
    head = git(maw, "rev-parse", "HEAD").strip()
    policy = MawConfig.load(maw).raw
    prey_roots = [tmp_path / "source-a", tmp_path / "source-b"]
    for prey in prey_roots:
        prey.mkdir()

    def feed(prey: Target, maw: Path, options: EatOptions, **kwargs: object) -> EatResult:
        result = fake_eat(prey, maw, options, **kwargs)
        assert prey.path is not None
        path = result.out_dir / "menu.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        card = Candidate(
            "ci",
            f"ci.{prey.path.name}.cache",
            "Cache CI",
            "Synthetic cache finding",
            license_mode="COPY",
            origin="licensed",
            score=0.7,
            trace={"prey": prey.label, "sha": "a" * 40},
        )
        data["candidates"] = [card.to_dict()]
        data["maw"]["sha"] = head
        data["analysis_inputs"]["policy"] = policy
        data["receiving_context"].update(
            {
                "sha": head,
                "selected_ref": "main",
                "worktree": "clean",
                "policy": fingerprint(policy),
            }
        )
        path.write_text(json.dumps(data), encoding="utf-8")
        return result

    monkeypatch.setattr("hungry_crab.multifeed.eat", feed)
    bundle = eat_many(
        [Target(path=prey) for prey in prey_roots],
        maw,
        EatOptions(out=tmp_path / "verified-bundle", wiki=False),
    )
    menu, _, _ = load_multi(bundle.out_dir)
    assert menu["receiving_context_status"] == "observed"
    return maw, bundle.out_dir, [card["id"] for card in menu["candidates"]]


@pytest.mark.parametrize("change", ["worktree", "head", "selected_ref", "ignored_policy"])
def test_multi_issue_publication_refuses_stale_live_receiving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    maw, bundle, ids = _current_receiving_bundle(tmp_path, monkeypatch)
    if change == "worktree":
        (maw / "app.py").write_text("print('modified')\n", encoding="utf-8")
    elif change == "head":
        (maw / "app.py").write_text("print('committed')\n", encoding="utf-8")
        git(maw, "add", "app.py")
        git(maw, "commit", "-m", "change receiving HEAD")
    elif change == "selected_ref":
        git(maw, "checkout", "-b", "different")
    else:
        # Ignored policy changes do not necessarily affect Git worktree identity.
        (maw / ".crab.yml").write_text("mode: strict\n", encoding="utf-8")
    client = FakeIssues()
    ledger = Ledger.load(None, maw="maw")
    with pytest.raises(CrabError, match="receiving checkout or policy changed"):
        serve_many(
            bundle,
            maw,
            ServeOptions(mode="issue", ids=ids),
            config=MawConfig.load(maw),
            ledger=ledger,
            client=client,
        )
    assert not client.created and not client.labels and not ledger.entries


def test_multi_issue_rechecks_live_receiving_between_source_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    maw, bundle, ids = _current_receiving_bundle(tmp_path, monkeypatch)
    client = FakeIssues()
    ledger = Ledger.load(None, maw="maw")
    original_create = client.create

    def change_after_first(*args: object, **kwargs: object) -> str:
        result = original_create(*args, **kwargs)
        if len(client.created) == 1:
            (maw / "app.py").write_text("print('changed after first issue')\n", encoding="utf-8")
        return result

    monkeypatch.setattr(client, "create", change_after_first)
    with pytest.raises(CrabError, match="receiving checkout or policy changed"):
        serve_many(
            bundle,
            maw,
            ServeOptions(mode="issue", ids=ids),
            config=MawConfig.load(maw),
            ledger=ledger,
            client=client,
        )
    # Do not invent rollback of an already-created remote issue.
    assert len(client.created) == 1
    assert len([entry for entry in ledger.entries.values() if entry.status == "served"]) == 1


def test_multi_issue_unchanged_receiving_context_can_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    maw, bundle, ids = _current_receiving_bundle(tmp_path, monkeypatch)
    client = FakeIssues()
    ledger = Ledger.load(None, maw="maw")
    report = serve_many(
        bundle,
        maw,
        ServeOptions(mode="issue", ids=ids),
        config=MawConfig.load(maw),
        ledger=ledger,
        client=client,
    )
    assert len(report.served) == len(client.created) == 2



def _repo_receipt_ledger(maw: Path) -> Ledger:
    # The repo path is the default, and the parent normally exists after crab init.
    path = MawConfig.load(maw).ledger_path()
    assert path is not None
    path.parent.mkdir(exist_ok=True)
    return Ledger.load(path, maw=str(maw))


@pytest.mark.parametrize("tamper", ["none", "receiving_source", "ledger_bytes"])
def test_multi_issue_repo_ledger_receipts_do_not_hide_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    maw, bundle, ids = _current_receiving_bundle(tmp_path, monkeypatch)
    client = FakeIssues()
    ledger = _repo_receipt_ledger(maw)
    config = MawConfig.load(maw)
    original_save = ledger.save
    save_count = 0

    def save_then_external_change(*, now: object = None) -> Path | None:
        nonlocal save_count
        result = original_save(now=now)
        save_count += 1
        if save_count == 1:
            if tamper == "receiving_source":
                (maw / "app.py").write_text("print('outside change')\n", encoding="utf-8")
            elif tamper == "ledger_bytes":
                assert ledger.path is not None
                ledger.path.write_bytes(ledger.path.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(ledger, "save", save_then_external_change)
    if tamper != "none":
        with pytest.raises(CrabError, match="receiving checkout or policy changed"):
            serve_many(
                bundle,
                maw,
                ServeOptions(mode="issue", ids=ids),
                config=config,
                ledger=ledger,
                client=client,
            )
        assert len(client.created) == save_count == 1
    else:
        report = serve_many(
            bundle,
            maw,
            ServeOptions(mode="issue", ids=ids),
            config=config,
            ledger=ledger,
            client=client,
        )
        assert len(report.served) == len(client.created) == save_count == 2
    assert ledger.path is not None and ledger.path.is_file()
    assert all(entry.status == "served" for entry in ledger.entries.values())


@pytest.mark.parametrize("tamper_ledger", [False, True])
def test_multi_pr_repo_ledger_receipt_recheck_between_publications(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper_ledger: bool
) -> None:
    maw, bundle, ids = _current_receiving_bundle(tmp_path, monkeypatch, serve_as="pr")
    ledger = _repo_receipt_ledger(maw)
    config = MawConfig.load(maw)
    config.serve.max_prs_per_run = 2
    published: list[str] = []
    original_save = ledger.save
    saves = 0

    def save_and_optionally_tamper(*, now: object = None) -> Path | None:
        nonlocal saves
        result = original_save(now=now)
        saves += 1
        if saves == 1 and tamper_ledger:
            assert ledger.path is not None
            ledger.path.write_bytes(ledger.path.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(ledger, "save", save_and_optionally_tamper)
    monkeypatch.setattr("hungry_crab.multiserve.decode_receipt_stream", lambda _: {})
    monkeypatch.setattr("hungry_crab.multiserve.load_cleanroom_receipts", lambda _: {})

    def plan_source(
        _meal: Path,
        _maw: Path,
        options: ServeOptions,
        *,
        publication_plans: list[PullRequestServePlan],
        before_publication: object,
        **_kwargs: object,
    ) -> ServeReport:
        assert before_publication is not None
        assert options.ids is not None
        cards = [
            Candidate(
                "ci",
                card_id.split(":")[-1],
                "Cache CI",
                "Synthetic cache finding",
                license_mode="COPY",
                serve_as="pr",
                origin="licensed",
                score=0.7,
            )
            for card_id in options.ids
        ]
        assert [card.id for card in cards] == list(options.ids)

        def publish(
            card: Candidate, _prepared: PreparedPullRequest, _allow_create: bool
        ) -> PullRequestPublication:
            before_publication()  # type: ignore[operator]
            published.append(card.id)
            return PullRequestPublication(
                "crab/fixture", f"https://github.com/example/maw/pull/{len(published)}", True
            )

        publication_plans.append(
            PullRequestServePlan(
                tuple((card, PreparedPullRequest("title", "body", ()), ()) for card in cards),
                (),
                publish,
            )
        )
        return ServeReport("pr-branch", str(maw))

    monkeypatch.setattr("hungry_crab.multiserve.serve", plan_source)
    if tamper_ledger:
        with pytest.raises(CrabError, match="receiving checkout or policy changed"):
            serve_many(
                bundle,
                maw,
                ServeOptions(mode="pr-branch", ids=ids),
                config=config,
                ledger=ledger,
            )
        assert len(published) == saves == 1
    else:
        report = serve_many(
            bundle,
            maw,
            ServeOptions(mode="pr-branch", ids=ids),
            config=config,
            ledger=ledger,
        )
        assert len(report.served) == len(published) == saves == 2
    assert ledger.path is not None and ledger.path.is_file()
