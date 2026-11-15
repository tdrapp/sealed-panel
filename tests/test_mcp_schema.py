"""The MCP tools' contract, frozen: a change to a tool's name, annotations or schemas fails here.

A host and its agent read these schemas; a change to them is a change to the interface, and it
must be deliberate. When it is, regenerate the snapshot and commit it with the change:

    SEALED_PANEL_UPDATE_MCP_SNAPSHOT=1 pytest tests/test_mcp_schema.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastmcp")

from fastmcp import Client

from sealed_panel.mcp_server import build_server

SNAPSHOT = Path(__file__).resolve().parent / "mcp_tools.json"
UPDATE = "SEALED_PANEL_UPDATE_MCP_SNAPSHOT"


async def listed(root: Path, mode: str) -> list[dict[str, Any]]:
    async with Client(build_server(root), mode=mode) as c:
        tools = await c.list_tools()
    return [
        {
            "name": t.name,
            "annotations": t.annotations.model_dump(mode="json", exclude_none=True)
            if t.annotations
            else None,
            "input_schema": t.input_schema,
            "output_schema": t.output_schema,
        }
        for t in tools
    ]


def text(tools: list[dict[str, Any]]) -> str:
    return json.dumps(tools, indent=2, sort_keys=True) + "\n"


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_the_tools_match_the_snapshot(tmp_path: Path, mode: str) -> None:
    now = text(await listed(tmp_path, mode))
    if os.environ.get(UPDATE) == "1" and mode == "auto":
        SNAPSHOT.write_text(now, encoding="utf-8")
    assert SNAPSHOT.exists(), f"no snapshot: run with {UPDATE}=1"
    then = SNAPSHOT.read_text(encoding="utf-8")
    if now != then:
        changed = [t["name"] for t in json.loads(now) if t not in json.loads(then)]
        pytest.fail(
            f"the MCP tools changed ({mode} mode): {changed or 'a tool was removed'}. If the "
            f"change is deliberate, rerun with {UPDATE}=1 and commit {SNAPSHOT.name}."
        )


def test_the_snapshot_holds_every_tool() -> None:
    names = [t["name"] for t in json.loads(SNAPSHOT.read_text(encoding="utf-8"))]
    assert len(names) == 10 and len(set(names)) == 10
    assert "judge" in names and "rubrics_use" in names
    assert not {"promote", "feedback", "seal", "void"} & set(names)
