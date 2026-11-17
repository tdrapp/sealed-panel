"""The agent skill has one source, `skill/`; the copies agent hosts read, under `hosts/`, must
match it byte for byte, and the plugin files must agree on the version they install. Each host
is found by what its folder holds, never by its name: this file stays vendor-neutral."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

from sealed_panel import __version__

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "skill"
HOSTS = ROOT / "hosts"
REPO = "https://github.com/tdrapp/sealed-panel.git"
# Every entry the repository tracks at its root: the project's own. Whatever a host reads sits
# in its folder under hosts/, but for the one file a host requires at the root: the marketplace
# file, in a dot folder named after that host (`.<host>-plugin`), which holds nothing else.
ROOT_ENTRIES = {
    ".editorconfig", ".github", ".gitignore", ".gitleaks.toml", ".pre-commit-config.yaml",
    "AGENTS.md", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE", "NOTICE", "README.md",
    "SECURITY.md", "docs", "examples", "hosts", "pyproject.toml", "skill", "src", "tests",
    "tools", "uv.lock",
}  # fmt: skip
PLUGIN_FOLDER = re.compile(r"\.[a-z0-9]+(?:-[a-z0-9]+)*-plugin")

if not SOURCE.is_dir():  # the mutation check runs on a copy of src, tests and examples only
    pytest.skip("not a checkout: the skill folders are not here", allow_module_level=True)


def files(folder: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir()) if p.is_file()}


def build_hosts() -> ModuleType:
    """`tools/build_hosts.py`, which writes the host copies from `skill/`."""
    path = ROOT / "tools" / "build_hosts.py"
    spec = importlib.util.spec_from_file_location("build_hosts", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def skill_copies() -> list[Path]:
    return sorted(HOSTS.glob("*/skills/sealed-panel"))


def plugin_dir() -> Path:
    """The host folder that is a plugin: it holds a manifest folder named after its host."""
    found = [p.parent.parent for p in HOSTS.glob("*/.*-plugin/plugin.json")]
    assert len(found) == 1, found
    return found[0]


def marketplace() -> Path:
    """The marketplace file, which its host requires at the repository root."""
    found = list(ROOT.glob(".*-plugin/marketplace.json"))
    assert len(found) == 1, found
    return found[0]


def test_each_host_has_a_copy_of_the_skill() -> None:
    copies = skill_copies()
    assert len(copies) == 2, copies  # the plugin's and the one for hosts reading skill folders
    for copy in copies:
        assert files(copy) == files(SOURCE), (
            f"{copy.relative_to(ROOT)} drifted from skill/: run python tools/build_hosts.py"
        )


def test_the_host_copies_are_what_the_build_writes() -> None:
    assert build_hosts().drift(ROOT) == [], "run python tools/build_hosts.py"


def test_the_root_tracks_only_the_project_and_the_marketplace() -> None:
    if not (ROOT / ".git").exists():  # an sdist: the next test still checks the plugin folder
        pytest.skip("not a git checkout: the tracked files are not known")
    files = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    top = {f.split("/", 1)[0] for f in files}
    plugin = {name for name in top if PLUGIN_FOLDER.fullmatch(name)}
    assert top - plugin <= ROOT_ENTRIES, (
        f"a host file at the root: {sorted(top - plugin - ROOT_ENTRIES)}"
    )
    assert len(plugin) == 1, sorted(plugin)
    inside = {f for f in files if f.split("/", 1)[0] in plugin}
    assert inside == {f"{name}/marketplace.json" for name in plugin}, sorted(inside)


def test_the_plugin_folder_at_the_root_holds_only_the_marketplace() -> None:
    folders = [p for p in ROOT.iterdir() if p.is_dir() and PLUGIN_FOLDER.fullmatch(p.name)]
    assert len(folders) == 1, folders
    assert [p.name for p in folders[0].iterdir()] == ["marketplace.json"]


def test_the_skill_has_its_name_and_description() -> None:
    front = (SOURCE / "SKILL.md").read_text(encoding="utf-8").split("---\n")[1]
    assert re.search(r"^name: sealed-panel$", front, re.MULTILINE)
    assert re.search(r"^description: \S.{40,}$", front, re.MULTILINE)


def test_the_judge_agent_carries_the_judge_instructions() -> None:
    agent = (plugin_dir() / "agents" / "eval-judge.md").read_text(encoding="utf-8")
    _, front, body = agent.split("---\n", 2)
    instructions = (SOURCE / "judge.md").read_text(encoding="utf-8").split("\n---\n", 1)[1]
    assert body.strip() == instructions.strip()
    assert "name: eval-judge" in front
    # Omitting `tools` would inherit every tool. The empty list gives none on the host we
    # measured; the deny list is a second layer, MCP tools included (a judge must not read
    # keys.json). A host that refuses the empty list fails loudly at launch.
    assert re.search(r"^tools: \[\]$", front, re.MULTILINE)
    [line] = re.findall(r"^disallowedTools: (.*)$", front, re.MULTILINE)
    denied = {name.strip() for name in line.split(",")}
    must = {"Read", "Glob", "Grep", "Bash", "PowerShell", "Edit", "Write", "NotebookEdit", "LSP"}
    must |= {"WebFetch", "WebSearch", "Agent", "Skill", "Monitor", "Workflow", "mcp__*"}
    assert must <= denied, must - denied


def test_the_plugin_files_install_one_version() -> None:
    folder = plugin_dir()
    [manifest] = folder.glob(".*-plugin/plugin.json")
    plugin = json.loads(manifest.read_text(encoding="utf-8"))
    market = json.loads(marketplace().read_text(encoding="utf-8"))
    server = json.loads((folder / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert plugin["name"] == "sealed-panel"
    [entry] = market["plugins"]
    assert entry["name"] == plugin["name"]
    # The plugin (skill, judge agent, server pin) installs from its folder at the release tag
    # the server is pinned to, never from main: main's skill may call tools the pinned server
    # lacks. A relative source would follow the branch the marketplace was added from.
    assert entry["source"] == {
        "source": "git-subdir",
        "url": REPO,
        "path": folder.relative_to(ROOT).as_posix(),
        "ref": f"v{__version__}",
    }
    assert entry["version"] == plugin["version"] == __version__
    launch = server["sealed-panel"]
    assert launch["command"] == "uvx"
    source = f"sealed-panel[mcp] @ git+https://github.com/tdrapp/sealed-panel@v{__version__}"
    assert launch["args"][:4] == ["--from", source, "sealed-panel", "mcp"]
    # The transcript folder comes from a value the host asks for when the plugin is enabled.
    assert launch["args"][4:] == ["--transcript-root", "${user_config.transcript_root}"]
    assert plugin["userConfig"]["transcript_root"]["type"] == "directory"
    # A real default: the host's projects folder under the home, which the server expands.
    default = plugin["userConfig"]["transcript_root"]["default"]
    assert re.fullmatch(r"~/\.[a-z]+/projects", default)


def test_the_host_configuration_file_starts_the_same_server() -> None:
    found = [
        tomllib.loads(p.read_text(encoding="utf-8"))
        for p in sorted(HOSTS.glob("*/*.toml"))
        if "mcp_servers" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, found
    block = found[0]["mcp_servers"]["sealed-panel"]
    launch = json.loads((plugin_dir() / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert [block["command"], *block["args"][:4]] == [
        launch["sealed-panel"]["command"],
        *launch["sealed-panel"]["args"][:4],
    ]
    assert block["args"][4] == "--transcript-root"
    assert block["tool_timeout_sec"] == 300
    assert isinstance(block["env_vars"], list)


def install_docs() -> list[Path]:
    """The files that carry an install line: the README, the integration guide, each host's."""
    found = [ROOT / "README.md", ROOT / "docs" / "integrations.md"]
    found += sorted(HOSTS.glob("*/README.md")) + sorted(HOSTS.glob("*/*.toml"))
    return [p for p in found if p.exists()]


def test_the_docs_pin_every_install_line_to_this_release() -> None:
    pattern = re.compile(r"sealed-panel(?:\[mcp\])?(?: @ git\+\S+@v|==)(\d+\.\d+\.\d+)")
    for path in install_docs():
        found = pattern.findall(path.read_text(encoding="utf-8"))
        assert found and set(found) == {__version__}, path.relative_to(ROOT)


def test_a_clone_line_in_a_host_readme_checks_out_this_release() -> None:
    # A clone of `main` would pair the skill of `main` with the server pinned to the release.
    lines = [
        line
        for path in sorted(HOSTS.glob("*/README.md"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if "git clone" in line
    ]
    assert lines
    for line in lines:
        assert f"git clone --branch v{__version__} " in line, line


def test_a_host_folder_names_no_other_release() -> None:
    # A host's README and configuration name the release they install (a tag to clone, a
    # server pin) and no other: one left behind at a release installs the old one.
    for path in [*HOSTS.glob("*/README.md"), *HOSTS.glob("*/*.toml")]:
        found = set(re.findall(r"\bv(\d+\.\d+\.\d+)\b", path.read_text(encoding="utf-8")))
        assert found == {__version__}, path.relative_to(ROOT)


def test_the_readme_names_this_version() -> None:
    path = ROOT / "README.md"
    if not path.exists():
        pytest.skip("not a checkout")
    found = re.search(r"^Version (\d+\.\d+\.\d+)\. .*\(CHANGELOG\.md\)", path.read_text(), re.M)
    assert found and found[1] == __version__


def test_no_uvx_line_runs_an_unpinned_server() -> None:
    for path in install_docs():
        text = path.read_text(encoding="utf-8")
        assert "--from 'sealed-panel[mcp]' " not in text, path.relative_to(ROOT)


def test_the_release_publishes_through_trusted_publishing() -> None:
    path = ROOT / ".github" / "workflows" / "release.yml"
    if not path.exists():
        pytest.skip("an sdist has no workflows")
    text = path.read_text(encoding="utf-8")
    publish = text[text.index("  publish:") :]
    assert "environment: pypi" in publish and "id-token: write" in publish
    assert "gh-action-pypi-publish" in publish
    build = text[: text.index("  publish:")]
    assert "id-token" not in build  # the job that runs the build holds no publishing right
    assert "password" not in text and "secrets." not in text
    assert 'tags: ["v*"]' in text


def test_every_action_is_pinned_by_commit() -> None:
    folder = ROOT / ".github" / "workflows"
    if not folder.exists():
        pytest.skip("an sdist has no workflows")
    pin = re.compile(r"^\s*(?:- )?uses: [\w./-]+@[0-9a-f]{40} # v\d[\w.]*$")
    lines = [
        (path.name, line)
        for path in sorted(folder.glob("*.yml"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if "uses:" in line
    ]
    assert lines
    assert [item for item in lines if not pin.match(item[1])] == []


def test_a_release_cannot_be_pushed_with_a_mutable_publisher() -> None:
    path = ROOT / ".github" / "workflows" / "release.yml"
    if not path.exists():
        pytest.skip("an sdist has no workflows")
    assert re.search(r"gh-action-pypi-publish@[0-9a-f]{40}", path.read_text(encoding="utf-8"))


def ci_check_names(text: str) -> set[str]:
    """Every check name the CI workflow produces: each job's name, expanded over its matrix."""
    names: set[str] = set()
    for block in re.split(r"\n(?=  [a-z][\w-]*:\n)", text):
        found = re.search(r"\n    name: (.+)", block)
        if not found:
            continue
        name = found[1].strip()
        axes = dict(re.findall(r"\n        ([\w-]+): \[(.*)\]", block))
        expanded = [name]
        for axis, values in axes.items():
            items = [v.strip().strip('"') for v in values.split(",")]
            token = "${{ matrix." + axis + " }}"
            expanded = [n.replace(token, v) for n in expanded for v in items]
        names.update(expanded)
    return names


def test_branch_protection_requires_checks_ci_produces() -> None:
    ci = ROOT / ".github" / "workflows" / "ci.yml"
    if not ci.exists():
        pytest.skip("an sdist has no workflows")
    produced = ci_check_names(ci.read_text(encoding="utf-8"))
    assert {"mutation (0/4)", "test (3.13, core)", "pip-audit"} <= produced  # the parse works
    protection = json.loads((ROOT / ".github" / "branch-protection.json").read_text())
    required = set(protection["required_status_checks"]["contexts"])
    assert required <= produced, required - produced  # a required check CI never reports
    # Deliberately not required: the shards (their gate `mutation` is) and pip-audit, so a
    # vulnerability published tomorrow does not block an unrelated pull request.
    assert produced - required == {f"mutation ({k}/4)" for k in range(4)} | {"pip-audit"}
