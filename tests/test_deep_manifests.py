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


def test_maven_property_expansion_is_bounded() -> None:
    """One property naming another thousands of times must not build a gigabyte string."""
    huge = "x" * 100_000
    source = (
        "<project><properties>"
        f"<a>{'${b}' * 25_000}</a><b>{huge}</b><ok>1.2.3</ok>"
        "</properties><dependencies>"
        "<dependency><groupId>g</groupId><artifactId>bomb</artifactId>"
        "<version>${a}</version></dependency>"
        "<dependency><groupId>g</groupId><artifactId>fine</artifactId>"
        "<version>${ok}</version></dependency>"
        "</dependencies></project>"
    )
    packages, info = parse_maven(source, "pom.xml")
    versions = {p.name: p.spec for p in packages}
    assert versions["g:fine"] == "1.2.3"
    assert len(versions["g:bomb"]) <= 256
    assert info["unresolved_versions"] == ["g:bomb"]


def test_gradle_parsing_is_linear_in_blank_lines() -> None:
    """A line anchor followed by a newline-matching space class is quadratic on a blank flood."""
    import time

    source = "\n" * 200_000 + "x\nimplementation 'g:a:1.0'\n"
    started = time.perf_counter()
    packages, _ = parse_gradle(source, "build.gradle")
    assert time.perf_counter() - started < 5
    assert [p.name for p in packages] == ["g:a"]


def test_gradle_unclosed_block_comments_are_linear() -> None:
    """Every `/*` without a `*/` used to rescan the rest of the file: 164 s for 256 KiB (#329)."""
    import time

    source = ("/*a" * 87_381)[:262_144]  # the deps miner's read limit
    started = time.perf_counter()
    packages, info = parse_gradle(source, "build.gradle")
    assert time.perf_counter() - started < 1
    assert packages == [] and info["unresolved_declarations"] == 0


def test_gradle_whitespace_after_a_configuration_is_linear() -> None:
    """`\\s*\\(?\\s*` split one whitespace run quadratically many ways before failing (#329)."""
    import time

    for gap in (" " * 262_000, "\n" * 262_000):
        started = time.perf_counter()
        packages, _ = parse_gradle("implementation" + gap + "x", "build.gradle")
        assert time.perf_counter() - started < 1
        assert packages == []


def test_gradle_comments_still_hide_and_only_hide_what_they_cover() -> None:
    packages, info = parse_gradle(
        "/* implementation 'fake:closed:1' */\n"
        "implementation  (  'org:a:1.0')\n"
        "  // api 'fake:line:1'\n"
        "api\n  'org:b:2.0' // see /* not a comment opener\n"
        "/* unclosed: not a comment, as before\n"
        "implementation 'org:after:1'\n",
        "build.gradle",
    )
    assert [p.name for p in packages] == ["org:a", "org:b", "org:after"]
    assert info["unresolved_declarations"] == 0


def test_gradle_comment_markers_inside_strings_are_text() -> None:
    """`exclude 'META-INF/*.kotlin_module'` opened a comment that hid every dependency."""
    android = """plugins { id 'com.android.application' }
android {
    namespace 'com.example.app'
    packagingOptions {
        exclude 'META-INF/*.kotlin_module'
    }
}
dependencies {
    implementation 'androidx.core:core-ktx:1.9.0'
    implementation 'com.squareup.retrofit2:retrofit:2.9.0'
    testImplementation 'junit:junit:4.13.2'
}
"""
    packages, info = parse_gradle(android, "app/build.gradle")
    assert [p.name for p in packages] == [
        "androidx.core:core-ktx",
        "com.squareup.retrofit2:retrofit",
        "junit:junit",
    ]
    assert info["unresolved_declarations"] == 0

    packages, _ = parse_gradle(
        'repositories { maven { url "https://repo.example/*" } }\n'
        "def note = '''a /* b\n// c'''\n"
        'def path = "x\\"/*"\n'
        "def quote = 'it\\'s /* here'\n"
        'packaging { resources.excludes += """/*.md"""\n}\n'
        "dependencies {\n"
        '    implementation("org:kts:1.0") /* trailing */\n'
        "    implementation 'org:groovy:2.0' // https://example.test/*\n"
        "}\n"
        "/* closed */\n",
        "build.gradle.kts",
    )
    assert [p.name for p in packages] == ["org:kts", "org:groovy"]


def test_gradle_hostile_literals_and_comments_are_linear() -> None:
    import time

    limit = 262_144
    for source in (
        "'\\'" * (limit // 3),  # an unclosed literal full of escaped quotes
        '"\\"' * (limit // 3),
        "'''" + "'' " * (limit // 3),  # an unclosed triple-quoted literal
        "/**/" * (limit // 4),
        "//\n" * (limit // 3),
        "'/*'" * (limit // 4) + "*/",
        "x /*" * (limit // 4),
    ):
        started = time.perf_counter()
        parse_gradle(source, "build.gradle")
        assert time.perf_counter() - started < 1, source[:12]
