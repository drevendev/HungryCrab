from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

from hungry_crab import cleanroom_guard
from hungry_crab.cleanroom_guard import CLEANROOM_AGENT_TYPE, cleanroom_guard_reason
from hungry_crab.prey_guard import strip_device_prefix

ROOT = Path(__file__).resolve().parents[1]


def _event(
    tool_input: object,
    *,
    agent_type: str = CLEANROOM_AGENT_TYPE,
    cwd: Path | None = None,
) -> dict[str, object]:
    event: dict[str, object] = {"agent_type": agent_type, "tool_input": tool_input}
    if cwd is not None:
        event["cwd"] = str(cwd)
    return event


def test_cleanroom_agent_denies_default_cache_paths() -> None:
    assert cleanroom_guard_reason(
        _event({"file_path": "~/.cache/hungry-crab/github/acme/prey/repo/src/core.py"})
    )
    assert cleanroom_guard_reason(
        _event({"path": r"C:\Users\alex\.cache\hungry-crab\github\acme\prey\repo"})
    )


def test_cleanroom_agent_denies_default_cache_traversal() -> None:
    path = Path.home() / ".cache" / "tmp" / ".." / "hungry-crab" / "github" / "acme" / "prey"
    assert cleanroom_guard_reason(_event({"file_path": str(path)}))


def test_cleanroom_agent_denies_literal_cache_environment_references() -> None:
    for command in (
        'cat "$CRAB_CACHE_DIR/github/acme/prey/repo/x.py"',
        "rg token ${CRAB_CACHE_DIR}/github/acme/prey/repo",
        r"type %CRAB_CACHE_DIR%\github\acme\prey\repo\x.py",
        r"Get-Content $env:CRAB_CACHE_DIR\github\acme\prey\repo\x.py",
    ):
        assert cleanroom_guard_reason(_event({"command": command}))


def test_cleanroom_agent_denies_configured_cache_root(monkeypatch, tmp_path: Path) -> None:
    cache = tmp_path / "crab-prey"
    monkeypatch.setenv("CRAB_CACHE_DIR", str(cache))
    assert cleanroom_guard_reason(
        _event({"query": "needle", "path": str(cache / "github" / "acme" / "prey" / "repo")})
    )


def test_cleanroom_agent_denies_configured_cache_traversal(monkeypatch, tmp_path: Path) -> None:
    cache = tmp_path / "crab-prey"
    monkeypatch.setenv("CRAB_CACHE_DIR", str(cache))
    traversed = cache.parent / "other" / ".." / cache.name / "github" / "acme" / "prey"
    assert cleanroom_guard_reason(_event({"path": str(traversed)}))


def test_cleanroom_agent_denies_relative_traversal_against_hook_cwd(
    monkeypatch, tmp_path: Path
) -> None:
    cache = tmp_path / "cache" / "crab-prey"
    cwd = tmp_path / "maw"
    monkeypatch.setenv("CRAB_CACHE_DIR", str(cache))
    relative = Path("..") / "cache" / "other" / ".." / "crab-prey" / "github" / "acme"
    assert cleanroom_guard_reason(_event({"path": str(relative)}, cwd=cwd))


def test_cleanroom_agent_allows_sibling_with_cache_prefix(monkeypatch, tmp_path: Path) -> None:
    cache = tmp_path / "crab-prey"
    monkeypatch.setenv("CRAB_CACHE_DIR", str(cache))
    sibling = cache.parent / f"{cache.name}-not-cache" / "README.md"
    assert cleanroom_guard_reason(_event({"file_path": str(sibling)})) is None


def test_cleanroom_hook_runs_the_packaged_guard_through_the_launcher() -> None:
    """The clean-room hook runs this module from the plugin's own ``src/``.

    ``hooks/guard.py`` maps ``cleanroom`` to the module the ``crab-cleanroom-guard`` console
    script names, so the hook and the manual command are one guard; the launcher itself is
    covered in ``tests/test_hooks.py``.
    """
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    handler = hooks["hooks"]["PreToolUse"][0]["hooks"][0]
    assert '"${CLAUDE_PLUGIN_ROOT}/hooks/guard.py"' in handler["command"]
    assert '"$g" cleanroom;' in handler["command"]
    assert "args" not in handler
    launcher = (ROOT / "hooks" / "guard.py").read_text(encoding="utf-8")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]
    assert '"cleanroom": "hungry_crab.cleanroom_guard"' in launcher
    assert scripts["crab-cleanroom-guard"] == "hungry_crab.cleanroom_guard:main"


def test_cleanroom_agent_may_use_maw_and_spec() -> None:
    assert (
        cleanroom_guard_reason(_event({"file_path": "/work/maw/.crab/specs/crab-tests-fixture.md"}))
        is None
    )


def test_guard_does_not_change_other_agents() -> None:
    assert (
        cleanroom_guard_reason(
            _event(
                {"file_path": "~/.cache/hungry-crab/github/acme/prey/repo/README.md"},
                agent_type="crab:crab-historian",
            )
        )
        is None
    )


def test_cleanroom_agent_fails_closed_when_tool_input_cannot_be_inspected() -> None:
    assert cleanroom_guard_reason(_event(None))


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A home with a default cache and a maw beside it; ``CRAB_CACHE_DIR`` is a custom root."""
    home = (tmp_path / "home").resolve()
    for directory in (
        home / ".cache" / "hungry-crab" / "github" / "acme" / "prey" / "repo",
        home / "data" / "crab-prey" / "github" / "acme" / "prey" / "repo",
        home / "work" / "maw" / "src",
    ):
        directory.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CRAB_CACHE_DIR", str(home / "data" / "crab-prey"))
    return home


def _search(tool: str, tool_input: dict[str, object], cwd: Path) -> dict[str, object]:
    event = _event(tool_input, cwd=cwd)
    event["tool_name"] = tool
    return event


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Grep", {"pattern": "def ", "path": "{home}/data"}),
        ("Grep", {"pattern": "def ", "path": "{home}/.cache", "output_mode": "content"}),
        ("Grep", {"pattern": "def ", "path": "{home}/.cache", "glob": "hungry-crab/**/*.py"}),
        ("Grep", {"pattern": "def ", "path": "{home}"}),
        ("Grep", {"pattern": "def ", "path": "../.."}),
        ("Grep", {"pattern": "def ", "path": "~"}),
        ("Glob", {"path": "{home}", "pattern": ".cache/hungry-*/**/*.py"}),
        ("Glob", {"pattern": "{home}/.cache/*crab/**/*.py"}),
        ("Glob", {"pattern": "~/.cache/*crab/**/*.py"}),
        ("Glob", {"pattern": "../../data/crab-*/**"}),
        ("Glob", {"pattern": "../../.cache/{a,hungry-crab}/github/**"}),
        ("Glob", {"path": "{home}/.cache", "pattern": "*.py"}),
    ],
)
def test_cleanroom_agent_denies_a_search_scope_that_holds_the_cache(
    tool: str, tool_input: dict[str, str], home: Path
) -> None:
    """HungryCrab#314: Grep and Glob are recursive, so a scope above the cache searches it."""
    concrete = {key: value.replace("{home}", home.as_posix()) for key, value in tool_input.items()}
    assert cleanroom_guard_reason(_search(tool, concrete, home / "work" / "maw"))


def test_cleanroom_agent_denies_grep_without_a_path_from_above_the_cache(home: Path) -> None:
    assert cleanroom_guard_reason(_search("Grep", {"pattern": "def "}, home))


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Grep", {"pattern": "def ", "path": "{home}/work/maw"}),
        ("Grep", {"pattern": "def ", "path": "src"}),
        ("Grep", {"pattern": "def "}),
        ("Glob", {"pattern": "**/*.py"}),
        ("Glob", {"path": "{home}/work/maw", "pattern": "**/*.py"}),
        ("Glob", {"pattern": "{home}/work/maw/src/*.py"}),
        ("Glob", {"pattern": "../maw/**/*.md"}),
        ("Read", {"file_path": "{home}/work/maw/src/core.py"}),
    ],
)
def test_cleanroom_agent_may_still_search_the_maw_beside_the_cache(
    tool: str, tool_input: dict[str, str], home: Path
) -> None:
    concrete = {key: value.replace("{home}", home.as_posix()) for key, value in tool_input.items()}
    assert cleanroom_guard_reason(_search(tool, concrete, home / "work" / "maw")) is None


def test_glob_from_the_home_directory_stays_out_of_the_cache_when_it_can(home: Path) -> None:
    """A pattern whose components cannot line up with the root's is not a cache search."""
    for pattern in ("work/**/*.py", "*.md", "data/other/**"):
        assert cleanroom_guard_reason(_search("Glob", {"pattern": pattern}, home)) is None


def _short_name(path: Path) -> str | None:
    """The 8.3 alias Windows gives ``path``, or ``None`` where the volume makes none."""
    if sys.platform != "win32":
        return None
    import ctypes

    buffer = ctypes.create_unicode_buffer(1024)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer)):
        return None
    return buffer.value if buffer.value.casefold() != str(path).casefold() else None


@pytest.mark.parametrize(
    ("spelling", "named"),
    [
        ("\\\\?\\C:\\crab-prey\\x", "C:\\crab-prey\\x"),
        ("\\\\.\\C:\\crab-prey\\x", "C:\\crab-prey\\x"),
        ("//?/C:/crab-prey/x", "C:/crab-prey/x"),
        ("\\??\\C:\\crab-prey\\x", "C:\\crab-prey\\x"),
        ("\\\\?\\UNC\\host\\share\\x", "\\\\host\\share\\x"),
        ("\\\\?\\unc\\host\\share\\x", "\\\\host\\share\\x"),
        ("\\\\host\\share\\x", "\\\\host\\share\\x"),
        ("C:\\crab-prey\\x", "C:\\crab-prey\\x"),
    ],
)
def test_a_device_prefix_names_the_win32_path(spelling: str, named: str) -> None:
    assert strip_device_prefix(spelling) == named


@pytest.mark.skipif(sys.platform != "win32", reason="device paths and 8.3 names are Windows'")
def test_cleanroom_agent_denies_device_and_short_spellings_of_the_default_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    root = home / ".cache" / "hungry-crab"
    (root / "github" / "acme" / "prey" / "repo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CRAB_CACHE_DIR", raising=False)
    short = _short_name(root)
    if short is None:
        pytest.skip("this volume does not generate 8.3 names")
    inside = "\\github\\acme\\prey\\repo\\core.py"
    assert "HUNGRY~" in short.upper()
    assert cleanroom_guard_reason(_event({"file_path": "\\\\?\\" + short + inside}))
    assert cleanroom_guard_reason(_event({"file_path": "\\\\.\\" + short + inside}))
    assert cleanroom_guard_reason(_event({"file_path": short + inside}))


@pytest.mark.skipif(sys.platform != "win32", reason="device paths are Windows'")
def test_cleanroom_agent_denies_a_device_spelling_of_a_custom_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "crab-prey"
    monkeypatch.setenv("CRAB_CACHE_DIR", str(cache))
    path = "\\\\?\\" + str(cache / "github" / "acme" / "prey" / "repo" / "core.py")
    assert cleanroom_guard_reason(_event({"pattern": "token", "path": path}))
    sibling = "\\\\?\\" + str(tmp_path / "maw" / "core.py")
    assert cleanroom_guard_reason(_event({"pattern": "token", "path": sibling})) is None


def _windows_spellings(target: Path) -> dict[str, str]:
    """Device and share spellings Windows opens as ``target``."""
    import ctypes

    volume = ctypes.create_unicode_buffer(260)
    ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(target.anchor, volume, 260)
    device = ctypes.create_unicode_buffer(260)
    ctypes.windll.kernel32.QueryDosDeviceW(target.drive, device, 260)
    tail = str(target)[3:]
    letter = target.drive[0]
    return {
        "volume-guid": volume.value + tail,
        "globalroot": "\\\\?\\GLOBALROOT" + device.value + "\\" + tail,
        "dot-globalroot": "\\\\.\\GLOBALROOT" + device.value + "\\" + tail,
        "localhost-admin-share": f"\\\\localhost\\{letter}$\\{tail}",
        "loopback-admin-share": f"\\\\127.0.0.1\\{letter}$\\{tail}",
        "unc-device-localhost": f"\\\\?\\UNC\\localhost\\{letter}$\\{tail}",
    }


@pytest.mark.skipif(sys.platform != "win32", reason="device and share paths are Windows'")
def test_cleanroom_agent_denies_device_and_share_spellings_of_the_cache(home: Path) -> None:
    """HungryCrab#188: Read and Grep open all of these; each is the cache under another name."""
    target = home / "data" / "crab-prey" / "github" / "acme" / "prey" / "repo" / "core.py"
    target.write_text("SECRET = 1\n", encoding="utf-8")
    for name, spelling in _windows_spellings(target).items():
        for tool, key in (("Read", "file_path"), ("Grep", "path")):
            event = _search(tool, {key: spelling, "pattern": "SECRET"}, home / "work" / "maw")
            assert cleanroom_guard_reason(event), f"{name} {tool}"


@pytest.mark.skipif(sys.platform != "win32", reason="device and share paths are Windows'")
def test_cleanroom_agent_refuses_any_share_it_cannot_reduce_without_asking_the_network(
    home: Path,
) -> None:
    import time

    maw_file = home / "work" / "maw" / "src" / "core.py"
    maw_file.write_text("x = 1\n", encoding="utf-8")
    start = time.perf_counter()
    for spelling in ("\\\\nohost-crab\\share\\x.py", "//nohost-crab/share/x.py"):
        event = _search("Read", {"file_path": spelling}, home / "work" / "maw")
        assert cleanroom_guard_reason(event), spelling
    assert time.perf_counter() - start < 0.5
    # A device spelling of a maw file comes down to its drive path and stays allowed.
    for spelling in ("\\\\?\\" + str(maw_file), _windows_spellings(maw_file)["volume-guid"]):
        event = _search("Read", {"file_path": spelling}, home / "work" / "maw")
        assert cleanroom_guard_reason(event) is None, spelling


@pytest.mark.skipif(sys.platform != "win32", reason="device and share paths are Windows'")
def test_cleanroom_agent_reduces_a_device_glob_before_matching_it(home: Path) -> None:
    """A Glob pattern under `//?/GLOBALROOT/...` or `//?/Volume{...}` is the drive it names."""
    import ctypes

    parent = home / "data"  # holds the custom root, crab-prey
    volume = ctypes.create_unicode_buffer(260)
    ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(parent.anchor, volume, 260)
    device = ctypes.create_unicode_buffer(260)
    ctypes.windll.kernel32.QueryDosDeviceW(parent.drive, device, 260)
    rest = parent.as_posix()[3:]
    letter = parent.drive[0]
    cwd = home / "work" / "maw"
    for pattern in (
        f"//?/GLOBALROOT{device.value.replace(chr(92), '/')}/{rest}/*/github/**",
        f"//?/GLOBALROOT/??/{letter}:/{rest}/*/github/**",
        f"//./GLOBALROOT/??/{letter}:/{rest}/crab-*/**",
        f"//?/{letter}:/{rest}/*/github/**",
        volume.value.replace("\\", "/") + f"{rest}/crab-*/**",
        "//nohost-crab/share/*/**",
    ):
        assert cleanroom_guard_reason(_search("Glob", {"pattern": pattern}, cwd)), pattern
    maw = cwd.as_posix()[3:]
    allowed = f"//?/{letter}:/{maw}/**/*.py"
    assert cleanroom_guard_reason(_search("Glob", {"pattern": allowed}, cwd)) is None


def test_cleanroom_agent_denies_a_path_it_cannot_canonicalise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CRAB_CACHE_DIR", str(tmp_path / "crab-prey"))
    event = _event({"file_path": str(tmp_path / "maw" / "core.py")})
    assert cleanroom_guard_reason(event) is None

    def unresolvable(path: Path, *, cwd: Path) -> Path:
        raise ValueError("embedded null character")

    monkeypatch.setattr(cleanroom_guard, "canonical_path", unresolvable)
    assert cleanroom_guard_reason(event)


def test_text_that_starts_with_a_tilde_is_not_an_unjudgeable_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`Path.expanduser()` raises for a `~name` it cannot place; that is text, not a refusal."""
    monkeypatch.setenv("CRAB_CACHE_DIR", str(tmp_path / "crab-prey"))
    monkeypatch.setenv("USERNAME", "someone-else")  # makes Windows give up on `~name` too
    for text in ("~~struck~~", "~nosuchuser-zz/notes.md", "~ approx"):
        edit = {"file_path": str(tmp_path / "maw" / "README.md"), "old_string": "x"}
        assert cleanroom_guard_reason(_event({**edit, "new_string": text})) is None, text


def test_cleanroom_agent_denies_the_configured_root_spelled_literally(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A second detector, so that canonicalisation is not the only line."""
    cache = tmp_path / "crab-prey"
    monkeypatch.setenv("CRAB_CACHE_DIR", str(cache))
    text = f"see {cache.as_posix()}/github/acme/prey/repo/core.py"
    assert cleanroom_guard_reason(_event({"content": text}))
