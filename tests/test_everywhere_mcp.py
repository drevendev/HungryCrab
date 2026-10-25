from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from hungry_crab.cache import Slug, Target
from hungry_crab.compare import MEAL_FILES, compare_for_maw, load_menu
from hungry_crab.digest import DigestOptions
from hungry_crab.errors import UsageError
from hungry_crab.mcp_service import AnalysisService


def test_local_analysis_pages_menu_and_no_source_or_ledger_writes(
    npm_app: Path, pyproject_cli: Path, tmp_path: Path
) -> None:
    service = AnalysisService([npm_app, pyproject_cli], tmp_path / "cache")
    before = {
        root: {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        for root in (npm_app, pyproject_cli)
    }
    digest = service.digest(str(npm_app))
    assert digest["pages"] and digest["untrusted_data"]
    page = service.digest(str(npm_app), page="inventory.md")
    assert "Inventory" in page["page"]["text"]
    menu = service.menu(str(npm_app), str(pyproject_cli), limit=1)
    assert len(menu["candidates"]) == 1 and menu["next_offset"] == 1
    rest = service.menu(str(npm_app), str(pyproject_cli), offset=1, limit=100)
    assert menu["total"] == 1 + len(rest["candidates"])
    assert rest["next_offset"] is None
    assert menu["side_effects"]["ledger_written"] is False
    assert menu["meal_dir"] is None
    for root, files in before.items():
        assert {
            p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
        } == files
    assert not (pyproject_cli / ".crab" / "ledger.json").exists()
    with pytest.raises(UsageError, match="listed digest"):
        service.digest(str(npm_app), page="../LICENSE")


def test_mcp_menu_cannot_replace_cli_meal_with_underdeduplicated_candidates(
    npm_app: Path,
    pyproject_cli: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP's shallow, no-issues view must not replace what crab menu/serve consume."""
    cache = tmp_path / "cache"
    options = DigestOptions(cache_root=cache)
    target = Target(path=npm_app)
    first, *_ = compare_for_maw(target, pyproject_cli, digest_options=options, record_ledger=False)
    nutrient_id = first.menu["candidates"][0]["id"]
    # Provide a deterministic local maw slug so issue dedup runs without a provider call.
    monkeypatch.setattr("hungry_crab.compare.maw_slug", lambda _root: Slug.parse("owner/maw"))
    cli, *_ = compare_for_maw(
        target,
        pyproject_cli,
        digest_options=options,
        record_ledger=False,
        issue_lookup=lambda _slug, _label: {nutrient_id: {"number": 7, "state": "open"}},
    )
    assert cli.meal_dir is not None
    saved = {name: (cli.meal_dir / name).read_bytes() for name in MEAL_FILES}
    before = load_menu(cli.meal_dir)
    assert before is not None
    assert nutrient_id in {item["id"] for item in before["hidden"]}
    assert nutrient_id not in {item["id"] for item in before["candidates"]}

    mcp = AnalysisService([npm_app, pyproject_cli], cache).menu(
        str(npm_app), str(pyproject_cli), limit=100
    )
    assert mcp["meal_dir"] is None
    assert nutrient_id in {item["id"] for item in mcp["candidates"]}
    assert {name: (cli.meal_dir / name).read_bytes() for name in MEAL_FILES} == saved
    assert load_menu(cli.meal_dir) == before


@pytest.mark.parametrize("target", ["../outside", "/outside", "", "example/prey"])
def test_scope_and_network_rejected_before_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    root = tmp_path / "approved"
    root.mkdir()
    service = AnalysisService([root], tmp_path / "cache")
    monkeypatch.setattr(
        "hungry_crab.mcp_service.run_digest", lambda *_: pytest.fail("analysis ran")
    )
    with pytest.raises(UsageError):
        service.digest(target)
    assert not service.cache.exists()


def test_approved_root_cannot_be_escaped_by_filesystem_alias(tmp_path: Path) -> None:
    root, outside = tmp_path / "approved", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    alias = root / "alias"
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(outside), str(alias))
    else:
        alias.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(UsageError, match="outside"):
            AnalysisService([root], tmp_path / "cache").local("alias")
    finally:
        if sys.platform == "win32":
            alias.rmdir()
        else:
            alias.unlink()


def test_roots_cache_and_menu_arguments_are_validated_before_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(UsageError, match="--root"):
        AnalysisService([], tmp_path / "cache")
    with pytest.raises(UsageError, match="outside"):
        AnalysisService([tmp_path], tmp_path / "cache")
    root = tmp_path / "root"
    root.mkdir()
    service = AnalysisService([root], tmp_path / "cache")
    monkeypatch.setattr(
        "hungry_crab.mcp_service.compare_for_maw",
        lambda *_args, **_kwargs: pytest.fail("compare ran"),
    )
    for kwargs in ({"category": "typo"}, {"limit": 0}, {"limit": 101}, {"offset": -1}):
        with pytest.raises(UsageError):
            service.menu("example/prey", str(root), **kwargs)


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_real_stdio_protocol_tools_schema_errors_and_scoped_digest(
    npm_app: Path, pyproject_cli: Path, tmp_path: Path, mode: str
) -> None:
    mcp = pytest.importorskip("mcp")
    import anyio
    from mcp.client.stdio import StdioServerParameters

    async def exercise() -> None:
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"GH_TOKEN", "GITHUB_TOKEN"}
        }
        env["PYTHONIOENCODING"] = "utf-8"
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "hungry_crab",
                "--cache-dir",
                str(tmp_path / "cache"),
                "mcp",
                "--root",
                str(npm_app),
                "--root",
                str(pyproject_cli),
            ],
            env=env,
            cwd=tmp_path,
        )
        with anyio.fail_after(90):
            async with mcp.Client(params, mode=mode, read_timeout_seconds=60) as client:
                listing = await client.list_tools()
                assert {tool.name for tool in listing.tools} == {"crab_digest", "crab_menu"}
                for tool in listing.tools:
                    assert tool.input_schema and tool.output_schema
                    assert tool.annotations.destructive_hint is False
                    assert tool.annotations.read_only_hint is False
                    assert tool.annotations.open_world_hint is False
                denied = await client.call_tool("crab_digest", {"prey": "example/prey"})
                assert denied.is_error
                result = await client.call_tool("crab_digest", {"prey": str(npm_app)})
                assert not result.is_error
                assert result.structured_content["manifest"]["schema"] == "hungry-crab.digest/1"
                menu = await client.call_tool(
                    "crab_menu", {"prey": str(npm_app), "maw": str(pyproject_cli), "limit": 2}
                )
                assert not menu.is_error and menu.structured_content["candidates"]
                assert not (pyproject_cli / ".crab" / "ledger.json").exists()
                unknown = await client.call_tool("crab_serve", {})
                assert unknown.is_error

    anyio.run(exercise)


@pytest.mark.parametrize(
    "target",
    [
        "\\\\attacker.example\\share\\repo",
        "//attacker.example/share/repo",
        "\\\\attacker@SSL\\share\\repo",
        "\\\\?\\UNC\\attacker.example\\share",
        "\\\\.\\pipe\\crab",
        "/\\attacker.example\\share",
    ],
)
def test_network_and_device_paths_are_refused_before_any_filesystem_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """On Windows, resolving a UNC path opens a connection and offers the user's credentials."""
    root = tmp_path / "approved"
    root.mkdir()
    service = AnalysisService([root], tmp_path / "cache")

    def touched(*_args: object, **_kwargs: object) -> None:
        pytest.fail("the path reached the filesystem")

    monkeypatch.setattr(Path, "resolve", touched)
    monkeypatch.setattr(Path, "exists", touched)
    for call in (service.local, service.prey):
        with pytest.raises(UsageError, match="network"):
            call(target)


def test_paths_leaving_the_roots_are_refused_before_any_filesystem_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative prey that escapes the roots may not be stated, existing or not.

    `local()` already refuses such a value lexically; `prey()` used to call `exists()` first,
    which let a client test whether an outside path exists by comparing the two errors.
    """
    root = tmp_path / "approved"
    root.mkdir()
    (tmp_path / "outside-secret-project").mkdir()
    service = AnalysisService([root], tmp_path / "cache")

    def touched(*_args: object, **_kwargs: object) -> None:
        pytest.fail("the path reached the filesystem")

    monkeypatch.setattr(Path, "resolve", touched)
    monkeypatch.setattr(Path, "exists", touched)
    with pytest.raises(UsageError, match="outside the operator-approved MCP roots"):
        service.local("x/../../outside-secret-project")
    with pytest.raises(UsageError, match="outside the operator-approved MCP roots"):
        service.prey("x/../../outside-secret-project")
    with pytest.raises(UsageError, match="outside the operator-approved MCP roots"):
        service.prey("x/../../no-such-project")


def _link_directory(link: Path, target: Path, kind: str) -> None:
    """Point ``link`` at ``target`` through a symlink or a native Windows junction."""
    if kind == "junction":
        if sys.platform != "win32":
            pytest.skip("junctions exist only on Windows")
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, (
            f"Windows junction creation unexpectedly failed: {completed.stderr[:300]}"
        )
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable here")


@pytest.mark.parametrize("secret_present", [True, False])
def test_implicit_traversal_cannot_probe_siblings_before_returning_inside_a_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, secret_present: bool
) -> None:
    approved = tmp_path / "approved"
    (approved / "anchor").mkdir(parents=True)
    (approved / "repo").mkdir()
    if secret_present:
        (tmp_path / "secret").mkdir()
    service = AnalysisService([approved], tmp_path / "cache")
    assert service.prey("repo").path == approved / "repo"

    def touched(*_args: object, **_kwargs: object) -> None:
        pytest.fail("implicit traversal reached the filesystem")

    with monkeypatch.context() as guard:
        guard.setattr(Path, "lstat", touched)
        guard.setattr(Path, "stat", touched)
        guard.setattr(Path, "exists", touched)
        guard.setattr(Path, "resolve", touched)
        with pytest.raises(UsageError) as error:
            service.prey("anchor/../../secret/../approved/repo")
    assert str(error.value) == "cannot parse repository reference with parent traversal"


@pytest.mark.parametrize("kind", ["symlink", "junction"])
@pytest.mark.parametrize("secret_present", [True, False])
def test_a_link_below_a_root_never_probes_an_outside_target(
    tmp_path: Path, kind: str, secret_present: bool
) -> None:
    """A link inside a root must not turn ``prey()`` into a probe of outside paths.

    ``approved/alias`` points at ``outside/mid``, so ``alias/../secret`` stays inside the
    root lexically while a following ``exists()`` would answer for ``outside/secret``.
    The refusal must be identical whether or not the outside target exists, and an
    ordinary link-free prey inside the root must keep working.
    """
    approved = tmp_path / "approved"
    outside = tmp_path / "outside"
    approved.mkdir()
    (outside / "mid").mkdir(parents=True)
    if secret_present:
        (outside / "secret").mkdir()
    _link_directory(approved / "alias", outside / "mid", kind)
    (approved / "plain-repo").mkdir()
    service = AnalysisService([approved], tmp_path / "cache")

    with pytest.raises(UsageError, match="cannot parse repository reference"):
        service.prey("alias/../secret")
    with pytest.raises(UsageError, match="remote prey requires"):
        service.prey("alias/secret")
    assert service.prey("plain-repo").path.name == "plain-repo"


def test_a_directory_inside_an_outer_repository_is_refused(tmp_path: Path) -> None:
    """Git looks upward: a plain directory under a root must not lend out a parent repository."""
    from fixture_builder import git as fixture_git

    outer = tmp_path / "outer"
    root = outer / "vendor-drop"
    root.mkdir(parents=True)
    fixture_git(outer, "init", "--quiet")
    service = AnalysisService([root], tmp_path / "cache")
    with pytest.raises(UsageError, match="git repository outside"):
        service.local(str(root))
    inner = root / "inner"
    inner.mkdir()
    fixture_git(inner, "init", "--quiet")
    assert service.local(str(inner)) == inner.resolve()


def test_check_runs_from_a_directory_that_contains_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`crab mcp --check` from the home directory, where the default cache lives."""
    pytest.importorskip("mcp")
    from hungry_crab.cli import main

    home = tmp_path / "home"
    cache = home / ".cache" / "hungry-crab"
    cache.mkdir(parents=True)
    monkeypatch.chdir(home)
    monkeypatch.setenv("CRAB_CACHE_DIR", str(cache))
    assert main(["mcp", "--check"]) == 0
    AnalysisService([], cache, validate=False)
