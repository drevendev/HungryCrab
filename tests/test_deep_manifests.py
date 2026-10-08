from __future__ import annotations

import json

from hungry_crab.digest import DigestResult
from hungry_crab.miners.deps import parse_composer, parse_go_mod, parse_gradle, parse_maven


def test_eight_ecosystems_in_one_fixture(digests: dict[str, DigestResult]) -> None:
    data = json.loads((digests["polyglot"].out_dir / "deps.json").read_text())
    assert set(data["ecosystems"]) == {
        "python",
        "npm",
        "dotnet",
        "rust",
        "go",
        "ruby",
        "jvm",
        "php",
    }
    names = {p["name"] for p in data["packages"]}
    assert {
        "psr/log",
        "org.junit.jupiter:junit-jupiter",
        "org.slf4j:slf4j-api",
        "rack",
        "serde",
    } <= names
    assert "example.com/excluded" not in names and "example.com/replaced" not in names


def test_go_only_reads_require_directives() -> None:
    source = (
        "module example.com/a\nrequire\texample.com/single v1.0.0\nrequire (\n"
        " example.com/direct v2.0.0 // indirect\n)\nexclude (\n example.com/excluded v1.0.0\n)\n"
        "replace (\n example.com/old v1.0.0 => example.com/new v2.0.0\n)\n"
    )
    packages, _ = parse_go_mod(source, "go.mod")
    assert [(p.name, p.kind) for p in packages] == [
        ("example.com/single", "runtime"),
        ("example.com/direct", "indirect"),
    ]


def test_maven_management_properties_and_unresolved_parent_versions() -> None:
    source = (
        '<project xmlns="urn:maven"><properties><v>1.2.3</v></properties>'
        "<dependencyManagement><dependencies><dependency><groupId>a</groupId>"
        "<artifactId>b</artifactId><version>${v}</version></dependency></dependencies>"
        "</dependencyManagement><dependencies><dependency><groupId>a</groupId>"
        "<artifactId>b</artifactId></dependency><dependency><groupId>a</groupId>"
        "<artifactId>c</artifactId><version>${parent.version}</version><scope>test</scope>"
        "</dependency></dependencies></project>"
    )
    packages, info = parse_maven(source, "pom.xml")
    assert packages[0].spec == "1.2.3" and packages[0].pinned
    assert packages[1].kind == "dev" and not packages[1].pinned
    assert info["unresolved_versions"] == ["a:c"]


def test_gradle_is_static_and_reports_unresolved_catalog_entries() -> None:
    packages, info = parse_gradle(
        """// implementation("fake:fake:1")
implementation("org:a:1.2.3")
testImplementation 'org:b:2.0'
implementation(libs.catalog)
""",
        "build.gradle",
    )
    assert [p.name for p in packages] == ["org:a", "org:b"]
    assert packages[1].kind == "dev"
    assert info["unresolved_declarations"] == 1


def test_composer_platform_is_not_a_package() -> None:
    packages, info = parse_composer(
        '{"require":{"php":"^8.2","ext-json":"*","a/b":"1.2.3"},"require-dev":{"a/test":"^2.0"}}',
        "composer.json",
    )
    assert [p.name for p in packages] == ["a/b", "a/test"]
    assert packages[0].pinned and not packages[1].pinned
    assert info["platform"] == {"php": "^8.2", "ext-json": "*"}


def test_rust_workspace_and_target_dependencies_do_not_guess_pins() -> None:
    from hungry_crab.miners.deps import parse_cargo

    packages, info = parse_cargo(
        '[workspace]\nmembers=["core"]\n[workspace.dependencies]\nserde="1"\n'
        "[dependencies]\nserde={workspace=true}\n"
        "[target.'cfg(unix)'.dependencies]\nlibc=\"=0.2.0\"\n",
        "Cargo.toml",
    )
    by_name = {p.name: p for p in packages}
    assert by_name["serde"].spec == "workspace" and by_name["serde"].pinned is None
    assert by_name["libc"].pinned is True and info["workspace_members"] == ["core"]
