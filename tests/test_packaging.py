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
    assert re.search(r"^tools: Read$", front, re.MULTILINE)  # never a tool that writes or runs


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
    assert launch["args"] == [
        "--from",
        f"sealed-panel[mcp]=={plugin['version']}",
        "sealed-panel",
        "mcp",
    ]


def test_the_documented_host_configuration_starts_the_same_server() -> None:
    doc = (ROOT / "docs" / "integrations.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```toml\n(.*?)```", doc, re.DOTALL)
    servers = [
        tomllib.loads(b)["mcp_servers"]["sealed-panel"] for b in blocks if "mcp_servers" in b
    ]
    assert len(servers) == 1
    [block] = servers
    launch = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert [block["command"], *block["args"]] == [
        launch["sealed-panel"]["command"],
        *launch["sealed-panel"]["args"],
    ]
    assert block["tool_timeout_sec"] == 300
    assert isinstance(block["env_vars"], list)
