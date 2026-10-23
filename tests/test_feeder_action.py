from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from fixture_builder import git

from hungry_crab.errors import CrabError

CALLER = "owner/caller"


def test_action_resolves_local_paths_in_the_caller_and_preserves_literal_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "feeder_action.py"
    spec = importlib.util.spec_from_file_location("feeder_action_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    workspace = tmp_path / "maw with spaces"
    prey = workspace / "donor;literal"
    prey.mkdir(parents=True)
    outputs = tmp_path / "outputs"
    summary = tmp_path / "summary"
    env = {
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(outputs),
        "GITHUB_STEP_SUMMARY": str(summary),
        "CRAB_PREY": "donor;literal",
        "CRAB_MAW": ".",
        "CRAB_SINCE": "all",
        "CRAB_ISSUES": "0",
        "CRAB_TOP": "30",
        "CRAB_MAX_REPO_KB": "307200",
        "CRAB_DEPTH": "normal",
        "CRAB_SHALLOW": "true",
        "CRAB_WIKI": "false",
        "CRAB_ALLOW_LOSS": "false",
        "CRAB_ALLOW_PRIVATE_ARTIFACT": "true",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    calls: list[list[str]] = []

    def fake_main(args: list[str]) -> int:
        calls.append(args)
        print(json.dumps({"out_dir": str(tmp_path / "bundle"), "counts": {"total": 2}}))
        return 0

    monkeypatch.setattr(module, "main", fake_main)
    assert module.run() == 0
    assert calls[0][calls[0].index("eat") + 1] == str(prey.resolve())
    assert calls[0][calls[0].index("--maw") + 1] == str(workspace)
    assert "candidate-count=2" in outputs.read_text()
    assert "2 candidates" in summary.read_text()


def _action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    prey: str,
    event: dict[str, object] | None,
    allow: str = "false",
    maw: str = ".",
    origin: str | None = f"https://github.com/{CALLER}",
) -> tuple[Any, list[list[str]]]:
    script = Path(__file__).resolve().parents[1] / "scripts" / "feeder_action.py"
    spec = importlib.util.spec_from_file_location("feeder_action_exposure", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    workspace = tmp_path / "workspace"
    (workspace / "donor").mkdir(parents=True, exist_ok=True)
    # actions/checkout leaves the calling repository at the workspace root with this origin.
    if not (workspace / ".git").exists():
        git(workspace, "init", "-q")
        git(workspace, "remote", "add", "origin", f"https://github.com/{CALLER}")
    if maw != "." and not (workspace / maw).exists():
        (workspace / maw).mkdir()
        if origin is not None:
            git(workspace / maw, "init", "-q")
            git(workspace / maw, "remote", "add", "origin", origin)
    env = {
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(tmp_path / "outputs"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "GITHUB_REPOSITORY": CALLER,
        "CRAB_PREY": prey,
        "CRAB_MAW": maw,
        "CRAB_SINCE": "all",
        "CRAB_ISSUES": "0",
        "CRAB_TOP": "30",
        "CRAB_MAX_REPO_KB": "307200",
        "CRAB_DEPTH": "normal",
        "CRAB_SHALLOW": "true",
        "CRAB_WIKI": "false",
        "CRAB_ALLOW_LOSS": "false",
        "CRAB_ALLOW_PRIVATE_ARTIFACT": allow,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    if event is None:
        monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    else:
        path = tmp_path / "event.json"
        path.write_text(json.dumps(event), encoding="utf-8")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    calls: list[list[str]] = []

    def fake_main(args: list[str]) -> int:
        calls.append(args)
        print(json.dumps({"out_dir": str(tmp_path / "bundle"), "counts": {"total": 1}}))
        return 0

    monkeypatch.setattr(module, "main", fake_main)
    return module, calls


@pytest.mark.parametrize("event", [{"repository": {"private": False}}, {}, None])
def test_a_public_caller_never_uploads_an_unconfirmed_prey(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event: dict[str, object] | None
) -> None:
    module, calls = _action(tmp_path, monkeypatch, prey="donor", event=event)
    assert module.run() == 2
    assert calls == [], "the refusal comes before any acquisition or digest"


def test_a_private_caller_or_an_explicit_opt_in_uploads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, calls = _action(
        tmp_path, monkeypatch, prey="donor", event={"repository": {"visibility": "private"}}
    )
    assert module.run() == 0 and len(calls) == 1
    module, calls = _action(
        tmp_path, monkeypatch, prey="donor", event={"repository": {"private": False}}, allow="true"
    )
    assert module.run() == 0 and len(calls) == 1


@pytest.mark.parametrize(("private", "code"), [(False, 0), (True, 2)])
def test_the_forge_decides_whether_a_remote_prey_is_public(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, private: bool, code: int
) -> None:
    module, calls = _action(
        tmp_path, monkeypatch, prey="owner/repo", event={"repository": {"private": False}}
    )

    class Client:
        def repo(self, _: object) -> dict[str, object]:
            return {"private": private}

    monkeypatch.setattr(module, "client_for", lambda _: Client())
    assert module.run() == code
    assert len(calls) == (1 if code == 0 else 0)


class _Forge:
    """A forge that knows the visibility of a few repositories and fails for the rest."""

    def __init__(self, known: dict[str, dict[str, object]]) -> None:
        self.known = known

    def repo(self, slug: object) -> dict[str, object]:
        if str(slug) not in self.known:
            raise CrabError(f"{slug} not found")
        return self.known[str(slug)]


_PUBLIC = {"private": False, "visibility": "public"}
_PRIVATE = {"private": True, "visibility": "private"}


@pytest.mark.parametrize(
    "maw_visibility",
    [_PRIVATE, None, {"private": False, "visibility": "internal"}, {}],
    ids=["private", "forge-error", "contradictory", "no-visibility"],
)
def test_a_public_caller_never_uploads_an_unconfirmed_maw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    maw_visibility: dict[str, object] | None,
) -> None:
    # A second checkout of a private repository, digested as the maw of a public caller.
    module, calls = _action(
        tmp_path,
        monkeypatch,
        prey="pypa/pipx",
        event={"repository": {"private": False}},
        maw="internal-maw",
        origin="https://github.com/owner/internal.git",
    )
    known = {"pypa/pipx": _PUBLIC}
    if maw_visibility is not None:
        known["owner/internal"] = maw_visibility
    monkeypatch.setattr(module, "client_for", lambda _: _Forge(known))
    assert module.run() == 2
    assert calls == [], "the refusal comes before any acquisition or digest"
    message = capsys.readouterr().err
    assert "the maw (internal-maw) is not verifiably public" in message
    assert "allow-private-artifact" in message


def test_a_tracked_directory_of_the_public_caller_uploads_as_a_maw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A monorepo package: `maw: packages/app` inside the public calling repository."""
    workspace = tmp_path / "workspace"
    (workspace / "packages" / "app").mkdir(parents=True)
    git(workspace, "init", "-q")
    git(workspace, "remote", "add", "origin", f"https://github.com/{CALLER}")
    (workspace / "packages" / "app" / "pyproject.toml").write_text("[project]\nname='app'\n")
    git(workspace, "add", "packages/app/pyproject.toml")
    git(workspace, "commit", "-q", "-m", "app")
    module, calls = _action(
        tmp_path,
        monkeypatch,
        prey="pypa/pipx",
        event={"repository": {"private": False}},
        maw="packages/app",
    )
    monkeypatch.setattr(module, "client_for", lambda _: _Forge({"pypa/pipx": _PUBLIC}))
    assert module.run() == 0
    assert len(calls) == 1


def test_an_untracked_directory_of_the_public_caller_is_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "build" / "maw").mkdir(parents=True)
    git(workspace, "init", "-q")
    git(workspace, "remote", "add", "origin", f"https://github.com/{CALLER}")
    (workspace / "build" / "maw" / "notes.txt").write_text("written by a previous step\n")
    module, calls = _action(
        tmp_path,
        monkeypatch,
        prey="pypa/pipx",
        event={"repository": {"private": False}},
        maw="build/maw",
    )
    monkeypatch.setattr(module, "client_for", lambda _: _Forge({"pypa/pipx": _PUBLIC}))
    assert module.run() == 2
    assert calls == []


def test_a_maw_without_its_own_repository_is_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A plain directory inside the caller's checkout must not borrow the caller's origin.
    module, calls = _action(
        tmp_path,
        monkeypatch,
        prey="pypa/pipx",
        event={"repository": {"private": False}},
        maw="downloaded",
        origin=None,
    )
    monkeypatch.setattr(module, "client_for", lambda _: _Forge({"pypa/pipx": _PUBLIC}))
    assert module.run() == 2
    assert calls == []


def test_both_unconfirmed_sides_are_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module, calls = _action(
        tmp_path, monkeypatch, prey="donor", event=None, maw="loose", origin=None
    )
    assert module.run() == 2
    assert calls == []
    assert "the prey and the maw (loose) are not verifiably public" in capsys.readouterr().err


def test_a_maw_the_forge_confirms_public_uploads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, calls = _action(
        tmp_path,
        monkeypatch,
        prey="pypa/pipx",
        event={"repository": {"private": False}},
        maw="other",
        origin="git@github.com:owner/other.git",
    )
    forge = _Forge({"pypa/pipx": _PUBLIC, "owner/other": _PUBLIC})
    monkeypatch.setattr(module, "client_for", lambda _: forge)
    assert module.run() == 0 and len(calls) == 1


def test_the_calling_repository_needs_its_visibility_confirmed_as_a_maw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without the event payload the caller's own checkout is confirmed by the forge, or refused.
    module, calls = _action(tmp_path, monkeypatch, prey="pypa/pipx", event={})
    monkeypatch.setattr(module, "client_for", lambda _: _Forge({"pypa/pipx": _PUBLIC}))
    assert module.run() == 2 and calls == []
    forge = _Forge({"pypa/pipx": _PUBLIC, CALLER: _PUBLIC})
    monkeypatch.setattr(module, "client_for", lambda _: forge)
    assert module.run() == 0 and len(calls) == 1


def test_a_private_maw_uploads_only_with_the_opt_in_or_a_private_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for event, allow in [
        ({"repository": {"private": False}}, "true"),
        ({"repository": {"visibility": "private"}}, "false"),
    ]:
        module, calls = _action(
            tmp_path,
            monkeypatch,
            prey="pypa/pipx",
            event=event,
            allow=allow,
            maw="internal-maw",
            origin="https://github.com/owner/internal",
        )
        forge = _Forge({"pypa/pipx": _PUBLIC, "owner/internal": _PRIVATE})
        monkeypatch.setattr(module, "client_for", lambda _, forge=forge: forge)
        assert module.run() == 0 and len(calls) == 1


def _caller_with_submodule(
    tmp_path: Path, *, origin: str | None = None, nested: bool = False
) -> Path:
    """A public caller checkout with a local repository added as the submodule vendor/private.

    The caller tracks a file of its own as well, so the workspace is a real tracked tree and the
    only entry the caller has under ``vendor/private`` is the gitlink (mode 160000). ``nested``
    gives that repository a populated submodule of its own, ``vendor/private/inner``, whose
    origin is a local path no forge can confirm.
    """
    private = tmp_path / "private-src"
    (private / "docs").mkdir(parents=True)
    git(private, "init", "-q")
    (private / "secret_plan.md").write_text("# Private roadmap\n", encoding="utf-8")
    (private / "docs" / "plan.md").write_text("# Plan\n", encoding="utf-8")
    git(private, "add", ".")
    git(private, "commit", "-q", "-m", "private")
    if nested:
        inner = tmp_path / "inner-src"
        inner.mkdir()
        git(inner, "init", "-q")
        (inner / "credentials.md").write_text("# Inner secrets\n", encoding="utf-8")
        git(inner, "add", ".")
        git(inner, "commit", "-q", "-m", "inner")
        git(
            private,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-q",
            str(inner),
            "inner",
        )
        git(private, "commit", "-q", "-m", "inner submodule")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git(workspace, "init", "-q")
    git(workspace, "remote", "add", "origin", f"https://github.com/{CALLER}")
    (workspace / "README.md").write_text("# public\n", encoding="utf-8")
    git(workspace, "add", "README.md")
    git(workspace, "commit", "-q", "-m", "init")
    git(
        workspace,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(private),
        "vendor/private",
    )
    git(workspace, "commit", "-q", "-m", "submodule")
    if nested:
        git(
            workspace,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "update",
            "-q",
            "--init",
            "--recursive",
        )
        assert (workspace / "vendor" / "private" / "inner" / "credentials.md").is_file()
    if origin is not None:
        git(workspace / "vendor" / "private", "remote", "set-url", "origin", origin)
    return workspace


@pytest.mark.parametrize("maw", ["vendor/private", "vendor/private/docs"])
def test_a_submodule_of_the_public_caller_is_not_a_tracked_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], maw: str
) -> None:
    """#386: the gitlink of a private submodule does not make it part of the public caller."""
    workspace = _caller_with_submodule(tmp_path)
    listing = git(workspace, "ls-files", "-s", "--", "vendor/private")
    assert listing.startswith("160000 "), "the caller tracks only the gitlink"
    module, calls = _action(
        tmp_path, monkeypatch, prey="pypa/pipx", event={"repository": {"private": False}}, maw=maw
    )
    monkeypatch.setattr(module, "client_for", lambda _: _Forge({"pypa/pipx": _PUBLIC}))
    assert module.maw_in_public_caller(workspace / maw) is False
    assert module.run() == 2
    assert calls == [], "the refusal comes before any acquisition or digest"
    assert f"the maw ({maw}) is not verifiably public" in capsys.readouterr().err


def test_a_submodule_maw_is_confirmed_by_its_own_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A submodule falls through to the forge check: public there, it uploads; else it does not."""
    _caller_with_submodule(tmp_path, origin="https://github.com/owner/vendored")
    for visibility, code in [(_PUBLIC, 0), (_PRIVATE, 2)]:
        module, calls = _action(
            tmp_path,
            monkeypatch,
            prey="pypa/pipx",
            event={"repository": {"private": False}},
            maw="vendor/private",
        )
        forge = _Forge({"pypa/pipx": _PUBLIC, "owner/vendored": visibility})
        monkeypatch.setattr(module, "client_for", lambda _, forge=forge: forge)
        assert module.run() == code
        assert len(calls) == (1 if code == 0 else 0)


def _public_maw_with_submodule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    maw: str,
    visibility: dict[str, object] | None,
    allow: str = "false",
) -> tuple[Any, list[list[str]]]:
    """The public caller's own checkout (or a tracked directory of it) as the maw.

    ``vendor/NOTICE`` makes ``vendor`` a tracked directory of the caller beside the submodule.
    ``visibility`` is what the forge says about the submodule's origin; None means it fails.
    """
    workspace = tmp_path / "workspace"
    (workspace / "vendor" / "NOTICE").write_text("vendored code\n", encoding="utf-8")
    git(workspace, "add", "vendor/NOTICE")
    git(workspace, "commit", "-q", "-m", "notice")
    module, calls = _action(
        tmp_path,
        monkeypatch,
        prey="pypa/pipx",
        event={"repository": {"private": False}},
        maw=maw,
        allow=allow,
    )
    known = {"pypa/pipx": _PUBLIC}
    if visibility is not None:
        known["owner/vendored"] = visibility
    monkeypatch.setattr(module, "client_for", lambda _: _Forge(known))
    return module, calls


@pytest.mark.parametrize(
    ("maw", "shown"), [(".", "vendor/private"), ("vendor", "private")], ids=["root", "tracked-dir"]
)
@pytest.mark.parametrize(
    ("origin", "visibility"),
    [(None, None), ("https://github.com/owner/vendored", _PRIVATE)],
    ids=["local-origin", "private-origin"],
)
def test_a_public_maw_carrying_a_populated_private_submodule_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    maw: str,
    shown: str,
    origin: str | None,
    visibility: dict[str, object] | None,
) -> None:
    """#386: the inventory walks into a checked-out submodule, so its visibility is asked too."""
    _caller_with_submodule(tmp_path, origin=origin)
    module, calls = _public_maw_with_submodule(
        tmp_path, monkeypatch, maw=maw, visibility=visibility
    )
    assert module.maw_is_public(tmp_path / "workspace" / maw) is True, "the maw itself is"
    assert module.run() == 2
    assert calls == [], "the refusal comes before any acquisition or digest"
    message = capsys.readouterr().err
    assert f"the submodule {shown} checked out in the maw ({maw}) is not verifiably public" in (
        message
    )
    assert "allow-private-artifact" in message


def test_an_uninitialised_submodule_is_nothing_to_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _caller_with_submodule(tmp_path)
    git(workspace, "submodule", "deinit", "-q", "-f", "vendor/private")
    assert (workspace / "vendor" / "private").is_dir()
    assert not any((workspace / "vendor" / "private").iterdir())
    module, calls = _public_maw_with_submodule(tmp_path, monkeypatch, maw=".", visibility=None)
    assert module.unconfirmed_submodules(workspace) == []
    assert module.run() == 0 and len(calls) == 1


def test_a_populated_submodule_the_forge_confirms_public_uploads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _caller_with_submodule(tmp_path, origin="https://github.com/owner/vendored")
    module, calls = _public_maw_with_submodule(tmp_path, monkeypatch, maw=".", visibility=_PUBLIC)
    assert module.run() == 0 and len(calls) == 1


def test_a_private_submodule_uploads_with_the_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _caller_with_submodule(tmp_path)
    module, calls = _public_maw_with_submodule(
        tmp_path, monkeypatch, maw=".", visibility=None, allow="true"
    )
    assert module.run() == 0 and len(calls) == 1


def test_a_public_submodule_still_answers_for_its_own_submodules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`submodules: recursive` checks out the private repositories a public one points at."""
    workspace = _caller_with_submodule(
        tmp_path, origin="https://github.com/owner/vendored", nested=True
    )
    module, calls = _public_maw_with_submodule(tmp_path, monkeypatch, maw=".", visibility=_PUBLIC)
    assert module.unconfirmed_submodules(workspace) == ["vendor/private/inner"]
    assert module.run() == 2 and calls == []
    assert "the submodule vendor/private/inner checked out" in capsys.readouterr().err


def test_uninitialised_gitlinks_do_not_count_against_the_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of #386: only a checked-out submodule is asked about, so only those count."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git(workspace, "init", "-q")
    git(workspace, "remote", "add", "origin", f"https://github.com/{CALLER}")
    (workspace / "README.md").write_text("# public\n", encoding="utf-8")
    git(workspace, "add", "README.md")
    entries: list[str] = []
    for index in range(257):
        entries += ["--cacheinfo", f"160000,{index + 1:040x},vendor/empty-{index:03d}"]
        if index % 2:
            (workspace / "vendor" / f"empty-{index:03d}").mkdir(parents=True)
    git(workspace, "update-index", "--add", *entries)
    git(workspace, "commit", "-q", "-m", "many uninitialised submodules")
    module, calls = _action(
        tmp_path, monkeypatch, prey="pypa/pipx", event={"repository": {"private": False}}
    )
    monkeypatch.setattr(module, "client_for", lambda _: _Forge({"pypa/pipx": _PUBLIC}))
    assert len(module._gitlinks(workspace)) == 257
    assert module.unconfirmed_submodules(workspace) == []
    assert module.run() == 0 and len(calls) == 1
