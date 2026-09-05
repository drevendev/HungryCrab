from __future__ import annotations

import pytest
from helpers import read_json

from hungry_crab.digest import DigestResult
from hungry_crab.miners.deps import (
    npm_pinned,
    parse_go_mod,
    parse_msbuild,
    python_pinned,
    split_python_requirement,
)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("1.2.3", True),
        ("=1.2.3", True),
        ("^1.2.3", False),
        ("~1.2", False),
        ("*", False),
        ("latest", False),
        ("workspace:*", None),
        ("github:foo/bar", None),
        ("npm:other@1.0.0", None),
    ],
)
def test_npm_pinned(spec: str, expected: bool | None) -> None:
    assert npm_pinned(spec) is expected


@pytest.mark.parametrize(
    ("req", "expected"),
    [
        ("click>=8.1", ("click", ">=8.1")),
        ("Requests[socks]==2.32.0 ; python_version < '3.13'", ("requests", "==2.32.0")),
        ("numpy", ("numpy", "")),
        ("-r other.txt", None),
        ("git+https://github.com/x/y", None),
        ("# comment", None),
    ],
)
def test_split_python_requirement(req: str, expected: tuple[str, str] | None) -> None:
    assert split_python_requirement(req) == expected
    if expected:
        assert python_pinned(expected[1]) == ("==" in expected[1])


def test_parse_msbuild_reads_references_and_properties() -> None:
    text = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFrameworks>net8.0;net9.0</TargetFrameworks>
    <OutputType>Exe</OutputType>
    <ManagePackageVersionsCentrally>true</ManagePackageVersionsCentrally>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="A" Version="1.0.0" />
    <PackageReference Include="B"><Version>2.*</Version></PackageReference>
    <PackageReference Include="C" />
    <ProjectReference Include="..\\Other\\Other.csproj" />
  </ItemGroup>
</Project>"""
    packages, info = parse_msbuild(text, "x.csproj")
    pinned = {p.name: p.pinned for p in packages}
    assert pinned == {"A": True, "B": False, "C": None}
    assert info["target_frameworks"] == ["net8.0", "net9.0"]
    assert info["output_type"] == "Exe"
    assert info["project_references"] == 1
    assert info["properties"]["ManagePackageVersionsCentrally"] == "true"


def test_parse_go_mod() -> None:
    text = (
        "module example.com/x\n\ngo 1.22\n\nrequire (\n"
        "\tgithub.com/a/b v1.2.3\n\tgolang.org/x/y v0.1.0 // indirect\n)\n"
    )
    packages, info = parse_go_mod(text, "go.mod")
    assert {(p.name, p.kind) for p in packages} == {
        ("github.com/a/b", "runtime"),
        ("golang.org/x/y", "indirect"),
    }
    assert info["go_version"] == "1.22"


def test_npm_dependency_policy(npm_digest: DigestResult) -> None:
    data = read_json(npm_digest, "deps.json")
    assert data["ecosystems"] == ["npm"]
    assert data["package_count"] == 10
    policy = data["policies"]["npm"]
    assert policy["package_manager"] == "pnpm"
    assert policy["lockfiles"] == ["pnpm-lock.yaml"]
    assert policy["pinned_ratio"] == 0.2
    assert policy["runtime"] == 2 and policy["dev"] == 8
    assert data["npm_scripts"]["test"] == "vitest run"
    assert data["engines"] == {"node": ">=20"}
    assert data["workspaces"] == []
    kinds = {p["name"]: p["kind"] for p in data["packages"]}
    assert kinds["zod"] == "runtime" and kinds["vitest"] == "dev"


def test_python_dependency_policy(py_digest: DigestResult) -> None:
    data = read_json(py_digest, "deps.json")
    assert data["ecosystems"] == ["python"]
    policy = data["policies"]["python"]
    assert policy["package_manager"] == "uv"
    assert policy["lockfiles"] == ["uv.lock"]
    assert data["build_backend"] == "hatchling.build"
    assert data["requires_python"] == ">=3.11"
    assert {"coverage", "mypy", "pytest", "ruff"} <= set(data["python_tools"])
    kinds = {p["name"]: p["kind"] for p in data["packages"]}
    assert kinds["click"] == "runtime"
    assert kinds["hypothesis"] == "group:dev"
    assert kinds["hatchling"] == "build"


def test_dotnet_dependency_policy(dotnet_digest: DigestResult) -> None:
    data = read_json(dotnet_digest, "deps.json")
    assert data["ecosystems"] == ["dotnet"]
    assert data["target_frameworks"] == ["net8.0", "net9.0"]
    policy = data["policies"]["dotnet"]
    assert policy["package_manager"] == "nuget"
    assert policy["pinned_ratio"] == 1.0
    assert policy["central_package_management"] is False
    names = {p["name"] for p in data["packages"]}
    assert {"Newtonsoft.Json", "xunit", "BenchmarkDotNet", "coverlet.collector"} <= names
    kinds = {m["path"]: m for m in data["manifests"]}
    assert kinds["src/Crustacean/Crustacean.csproj"]["properties"]["IsPackable"] == "true"
    assert kinds["tests/Crustacean.Tests/Crustacean.Tests.csproj"]["project_references"] == 1
