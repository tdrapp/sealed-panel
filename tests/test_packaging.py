"""The agent skill has one source, `skill/`; the copies agent hosts read must match it byte for
byte, and the plugin files must agree on the version they install."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

from sealed_panel import __version__

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "skill"
COPIES = [ROOT / "skills" / "sealed-panel", ROOT / ".agents" / "skills" / "sealed-panel"]

if not SOURCE.is_dir():  # the mutation check runs on a copy of src, tests and examples only
    pytest.skip("not a checkout: the skill folders are not here", allow_module_level=True)


def files(folder: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir()) if p.is_file()}


def manifest_dir() -> Path:
    """The plugin folder at the repository root (named after the host that reads it)."""
    found = [p for p in ROOT.glob(".*-plugin") if (p / "plugin.json").exists()]
    assert len(found) == 1, found
    return found[0]


@pytest.mark.parametrize("copy", COPIES, ids=lambda p: str(p.relative_to(ROOT)))
def test_each_skill_copy_matches_the_source(copy: Path) -> None:
    assert files(copy) == files(SOURCE), (
        f"{copy.relative_to(ROOT)} drifted from skill/: copy skill/* over it"
    )


def test_the_skill_has_its_name_and_description() -> None:
    front = (SOURCE / "SKILL.md").read_text(encoding="utf-8").split("---\n")[1]
    assert re.search(r"^name: sealed-panel$", front, re.MULTILINE)
    assert re.search(r"^description: \S.{40,}$", front, re.MULTILINE)


def test_the_judge_agent_carries_the_judge_instructions() -> None:
    agent = (ROOT / "agents" / "eval-judge.md").read_text(encoding="utf-8")
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
    folder = manifest_dir()
    plugin = json.loads((folder / "plugin.json").read_text(encoding="utf-8"))
    market = json.loads((folder / "marketplace.json").read_text(encoding="utf-8"))
    server = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert plugin["name"] == "sealed-panel"
    [entry] = market["plugins"]
    assert entry["name"] == plugin["name"] and entry["source"] == "./"
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


def test_the_documented_host_configuration_starts_the_same_server() -> None:
    doc = (ROOT / "docs" / "integrations.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```toml\n(.*?)```", doc, re.DOTALL)
    servers = [
        tomllib.loads(b)["mcp_servers"]["sealed-panel"] for b in blocks if "mcp_servers" in b
    ]
    assert len(servers) == 1
    [block] = servers
    launch = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert [block["command"], *block["args"][:4]] == [
        launch["sealed-panel"]["command"],
        *launch["sealed-panel"]["args"][:4],
    ]
    assert block["args"][4] == "--transcript-root"
    assert block["tool_timeout_sec"] == 300
    assert isinstance(block["env_vars"], list)


def test_the_docs_pin_every_install_line_to_this_release() -> None:
    pattern = re.compile(r"sealed-panel(?:\[mcp\])?(?: @ git\+\S+@v|==)(\d+\.\d+\.\d+)")
    for name in ("README.md", "docs/integrations.md"):
        path = ROOT / name
        if not path.exists():
            continue
        found = pattern.findall(path.read_text(encoding="utf-8"))
        assert found and set(found) == {__version__}, name


def test_no_uvx_line_runs_an_unpinned_server() -> None:
    for name in ("README.md", "docs/integrations.md"):
        path = ROOT / name
        if path.exists():
            assert "--from 'sealed-panel[mcp]' " not in path.read_text(encoding="utf-8"), name


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
