"""The MCP server, through an in-memory client in both protocol eras, and over stdio."""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastmcp")

from fastmcp import Client

from conftest import BAD, FLAT, Harness, judge_json, make_spec
from sealed_panel import mcp_server
from sealed_panel.config import load_config
from sealed_panel.ledger import results_by_judge
from sealed_panel.mcp_server import JUDGE_TOOL_TIMEOUT_S, build_server
from sealed_panel.spec import init_run
from sealed_panel.spec import run_config as run_config_of

STUB = Path(__file__).resolve().parent / "stub_judge.py"
TOOLS = [
    "init",
    "brief",
    "seal",
    "record",
    "void",
    "judge",
    "measure",
    "aggregate",
    "status",
    "report",
    "rubrics_list",
    "rubrics_suggest",
    "rubrics_show",
    "feedback",
]
READ_ONLY = {"status", "report", "rubrics_list", "rubrics_suggest", "rubrics_show"}


@pytest.fixture(params=["auto", "legacy"])
def mode(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def runner_config(h: Harness, *stub_args: str) -> Path:
    argv = [sys.executable, str(STUB), *stub_args]
    cfg = h.tmp / "judge.toml"
    cfg.write_text("[judge]\ncommand = [" + ", ".join(json.dumps(a) for a in argv) + "]\n")
    return cfg


def write_spec(h: Harness, **over: Any) -> Path:
    sp = h.tmp / "spec.json"
    sp.write_text(json.dumps(make_spec(**{"judges_per_persona": 2, "max_rounds": 1, **over})))
    return sp


async def call(c: Client, tool: str, **args: Any) -> dict[str, Any]:
    r = await c.call_tool_mcp(tool, args)
    assert not r.is_error, r.content
    assert r.structured_content is not None
    # The text content carries the same object as the structured content.
    assert json.loads(r.content[0].text) == r.structured_content  # type: ignore[union-attr]
    return dict(r.structured_content)


async def error(c: Client, tool: str, **args: Any) -> str:
    r = await c.call_tool_mcp(tool, args)
    assert r.is_error
    return str(r.content[0].text)  # type: ignore[union-attr]


class TestTools:
    async def test_tools_names_order_and_annotations(self, h: Harness, mode: str) -> None:
        async with Client(build_server(h.tmp), mode=mode) as c:
            tools = await c.list_tools()
        assert [t.name for t in tools] == TOOLS
        for t in tools:
            assert t.description and t.output_schema, t.name
            a = t.annotations
            assert a is not None
            if t.name in READ_ONLY:
                assert a.read_only_hint is True and a.open_world_hint is False, t.name
            else:
                assert a.read_only_hint is False and a.destructive_hint is False, t.name
                assert a.open_world_hint is (t.name == "judge"), t.name
        assert not {"promote", "reject", "rubrics_promote", "rubrics_reject"} & set(TOOLS)

    async def test_the_server_names_itself_and_the_protocol(self, h: Harness) -> None:
        async with Client(build_server(h.tmp), mode="legacy") as c:
            result = c.initialize_result
            assert result is not None
            assert result.server_info.name == "sealed-panel"
            assert str(result.instructions) == mcp_server.INSTRUCTIONS

    def test_the_judge_tool_has_an_explicit_timeout(self, h: Harness) -> None:
        assert JUDGE_TOOL_TIMEOUT_S == 3600.0


class TestEndToEnd:
    async def test_a_full_run_through_the_runner(self, h: Harness, mode: str) -> None:
        cfg = runner_config(h, "--score", "5")
        progress: list[tuple[float, float | None, str | None]] = []

        async def on_progress(p: float, total: float | None, message: str | None) -> None:
            progress.append((p, total, message))

        server = build_server(h.tmp, cfg)  # relative paths resolve against the root
        async with Client(server, mode=mode, progress_handler=on_progress) as c:
            write_spec(h)
            init = await call(c, "init", run="run", spec="spec.json")
            assert init["run"] == str(h.run.resolve()) and len(init["rubric_sha256"]) == 64
            b = await call(c, "brief", run="run", round=1, artefact="cand.md", control="ctrl.md")
            assert [Path(p).name for p in b["briefs"]] == ["brief_j1.md", "brief_j2.md"]
            j = await call(c, "judge", run="run", round=1)
            assert [s["status"] for s in j["seats"]] == ["VALID", "VALID"]
            assert all(s["source"] == "runner" and s["exit_code"] == 0 for s in j["seats"])
            assert [p[:2] for p in progress] == [(1, 2), (2, 2)]
            a = await call(c, "aggregate", run="run", round=1)
            assert a["feedback"].endswith("after_r1.md") and a["rejected_or_void"] == 0
            s = await call(c, "status", run="run")
            assert s["decision"] in {"CONVERGED", "CAP"}
            assert any(o.startswith("judge.command") for o in s["overrides"])
            assert s["details"]["next"] == "seal"
            await call(c, "seal", run="run", artefact="cand.md", control="ctrl.md")
            sealed = await call(c, "judge", run="run", sealed=True)
            assert all(x["status"] == "VALID" for x in sealed["seats"])
            agg = await call(c, "aggregate", run="run", sealed=True)
            assert agg["sealed"] is True and agg["feedback"] is None
            report = (await call(c, "report", run="run"))["markdown"]
            assert "Grade (sealed panel): clarity 5 (5/5); evidence 5 (5/5)" in report
            assert "read by sealed-panel from the judge command" in report
            fb = await call(c, "feedback", run="run", note="my read differs on clarity")
            assert fb["commands"][0].startswith(
                f"sealed-panel rubrics promote {fb['candidate_id']}"
            )
            assert Path(fb["path"]).exists()
            listed = await call(c, "rubrics_list", run="run")
            assert fb["candidate_id"] in [x["id"] for x in listed["candidates"]]

    async def test_host_mode_record_void_and_measure(self, h: Harness, mode: str) -> None:
        spec = make_spec(
            judges_per_persona=2,
            max_rounds=1,
            measured=[{"id": "length", "description": "words", "max": 100}],
        )
        (h.tmp / "spec.json").write_text(json.dumps(spec))
        async with Client(build_server(h.tmp), mode=mode) as c:
            await call(c, "init", run=str(h.run), spec=str(h.tmp / "spec.json"))
            await call(c, "brief", run="run", round=1, artefact="cand.md", control="ctrl.md")
            assert "no judge command" in await error(c, "judge", run="run", round=1)
            keys = h.keys(1)["judges"]
            meta = keys[0]
            sc = {r: BAD for r in meta["key"].values()} | {"candidate": FLAT}
            raw = judge_json(meta, h.texts(1, meta), sc)
            r = await call(c, "record", run="run", round=1, judge=1, agent_id="a1", reply=raw)
            assert r["status"] == "VALID" and r["source"] == "output"
            # A judge that got brief j2 and stopped: a void, which never fills the seat.
            t = h.tmp / "t.jsonl"
            brief = (h.run / "rounds" / "r1" / "brief_j2.md").read_text()
            row = {"type": "user", "agentId": "a3", "message": {"content": brief}}
            t.write_text(json.dumps(row) + "\n")
            v = await call(
                c, "void", run="run", round=1, judge=2, agent_id="a3", transcript="t.jsonl"
            )
            assert v["status"] == "VOID"
            meta = keys[1]
            sc = {r: BAD for r in meta["key"].values()} | {"candidate": FLAT}
            (h.tmp / "r2.json").write_text(judge_json(meta, h.texts(1, meta), sc))
            r = await call(
                c, "record", run="run", round=1, judge=2, agent_id="a2", reply_file="r2.json"
            )
            assert r["status"] == "VALID"
            m = await call(c, "measure", run="run", round=1, id="length", value="17")
            assert m == {"id": "length", "value": "17", "passed": True}
            await call(c, "aggregate", run="run", round=1)
            assert (await call(c, "status", run="run"))["decision"] == "CAP"

    async def test_rubrics_tools(self, h: Harness, mode: str) -> None:
        (h.tmp / "draft.md").write_text("Hi team, a short email about the build cache.")
        async with Client(build_server(h.tmp), mode=mode) as c:
            listed = await call(c, "rubrics_list")
            assert "message" in [t["id"] for t in listed["templates"]]
            s = await call(c, "rubrics_suggest", kind="email", text="draft.md")
            assert s["suggestions"][0]["id"] == "message" and s["all_templates"] == {}
            none = await call(c, "rubrics_suggest", kind="limerick")
            assert none["suggestions"] == [] and "message" in none["all_templates"]
            shown = await call(c, "rubrics_show", id="message")
            assert shown["template"]["dimensions"] and shown["candidates"] == []
            assert "no template 'nope'" in await error(c, "rubrics_show", id="nope")


class TestErrors:
    async def test_a_refusal_is_a_tool_error_with_its_reason(self, h: Harness, mode: str) -> None:
        async with Client(build_server(h.tmp), mode=mode) as c:
            msg = await error(c, "brief", run="run", round=1, artefact="a.md", control="b.md")
            assert "not initialised" in msg
            assert "rounds start at 1" not in msg
            msg = await error(c, "aggregate", run="run")
            assert "--round is required" in msg
            write_spec(h)
            await call(c, "init", run="run", spec="spec.json")
            assert "already frozen" in await error(c, "init", run="run", spec="spec.json")
            msg = await error(c, "record", run="run", round=1, judge=1, agent_id="x")
            assert "exactly one of transcript, reply_file or reply" in msg
            msg = await error(c, "status", run="missing")
            assert "not initialised" in msg

    async def test_a_missing_file_and_a_bad_file_are_reported(self, h: Harness, mode: str) -> None:
        async with Client(build_server(h.tmp), mode=mode) as c:
            assert "No such file" in await error(c, "init", run="r", spec="absent.json")
            write_spec(h)
            await call(c, "init", run="run", spec="spec.json")
            (h.run / "ledger.json").write_text("[]")
            assert "unusable input" in await error(c, "status", run="run")

    async def test_an_internal_error_is_masked(
        self, h: Harness, mode: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(run: Path) -> str:
            raise RuntimeError("internal detail /secret/path")

        monkeypatch.setattr(mcp_server, "render_report", boom)
        async with Client(build_server(h.tmp), mode=mode) as c:
            msg = await error(c, "report", run="run")
        assert "secret" not in msg and "report" in msg

    async def test_inputs_are_validated_strictly(self, h: Harness, mode: str) -> None:
        async with Client(build_server(h.tmp), mode=mode) as c:
            msg = await error(c, "brief", run="run", round="1", artefact="a", control="b")
            assert "valid integer" in msg
            assert await error(c, "rubrics_suggest", kind="cv", top=0)


class TestStdio:
    def _env(self) -> dict[str, str]:
        src = str(Path(__file__).resolve().parent.parent / "src")
        return {**os.environ, "PYTHONPATH": src + os.pathsep + os.environ.get("PYTHONPATH", "")}

    def test_the_server_answers_on_stdout_and_exits_when_stdin_closes(self, h: Harness) -> None:
        hello = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
        done = subprocess.run(
            [sys.executable, "-m", "sealed_panel", "mcp", "--root", str(h.tmp)],
            input=json.dumps(hello) + "\n",
            capture_output=True,
            text=True,
            timeout=60,
            env=self._env(),
            check=False,
        )
        assert done.returncode == 0, done.stderr
        lines = [ln for ln in done.stdout.splitlines() if ln.strip()]
        assert lines, done.stderr
        first = json.loads(lines[0])
        assert first["id"] == 1 and first["result"]["serverInfo"]["name"] == "sealed-panel"
        assert all(json.loads(ln)["jsonrpc"] == "2.0" for ln in lines)


class TestJudgeAuthority:
    """The judge comes from the configuration the user started the server with, never from
    the caller (the author)."""

    async def test_init_takes_no_configuration_from_the_caller(self, h: Harness, mode: str) -> None:
        evil = h.tmp / "evil.toml"
        evil.write_text('[judge]\ncommand = ["/bin/sh", "-c", "echo pwned"]\n')
        write_spec(h)
        async with Client(build_server(h.tmp, runner_config(h)), mode=mode) as c:
            assert await error(c, "init", run="run", spec="spec.json", config="evil.toml")
            assert not h.run.exists()
            await call(c, "init", run="run", spec="spec.json")
        assert run_config_of(h.run).judge["command"][1] == str(STUB)

    async def test_a_run_frozen_with_another_judge_is_refused(self, h: Harness, mode: str) -> None:
        evil = h.tmp / "evil.toml"
        sh = f"echo token=$FAKE_SECRET > {h.tmp / 'pwned.txt'}; exec {sys.executable} {STUB}"
        evil.write_text(
            "[judge]\ncommand = "
            + json.dumps(["/bin/sh", "-c", sh])
            + '\nenv_allow = ["FAKE_SECRET"]\n'
        )
        init_run(h.run, write_spec(h), load_config(evil))  # by hand, outside the server
        async with Client(build_server(h.tmp, runner_config(h)), mode=mode) as c:
            await call(c, "seal", run="run", artefact="cand.md", control="ctrl.md")
            msg = await error(c, "judge", run="run", sealed=True)
        assert "differ from the configuration the server was started with" in msg
        assert not (h.tmp / "pwned.txt").exists()

    async def test_a_server_without_a_judge_command_runs_no_judge(
        self, h: Harness, mode: str
    ) -> None:
        init_run(h.run, write_spec(h), load_config(runner_config(h)))
        async with Client(build_server(h.tmp), mode=mode) as c:
            await call(c, "seal", run="run", artefact="cand.md", control="ctrl.md")
            assert "differ from the configuration" in await error(
                c, "judge", run="run", sealed=True
            )


class TestConcurrentJudges:
    async def test_concurrent_judge_calls_give_one_reply_per_seat(
        self, h: Harness, mode: str
    ) -> None:
        cfg = runner_config(h, "--sleep", "1")
        write_spec(h, judges_per_persona=3)
        async with Client(build_server(h.tmp, cfg), mode=mode) as c:
            await call(c, "init", run="run", spec="spec.json")
            await call(c, "seal", run="run", artefact="cand.md", control="ctrl.md")
            calls = [c.call_tool_mcp("judge", {"run": "run", "sealed": True}) for _ in range(3)]
            await asyncio.gather(*calls)
        by = results_by_judge(h.run / "sealed" / "r1")
        detail = {
            j: [(r["ok"], r.get("void"), r["errors"][:1]) for r in rows] for j, rows in by.items()
        }
        assert {j: sum(r["ok"] for r in rows) for j, rows in by.items()} == {1: 1, 2: 1, 3: 1}, (
            detail
        )
        assert {j: len(rows) for j, rows in by.items()} == {1: 1, 2: 1, 3: 1}, detail

    async def test_a_retry_after_a_cancelled_call_never_judges_the_seat_twice(
        self, h: Harness, mode: str
    ) -> None:
        cfg = runner_config(h, "--sleep", "2")
        write_spec(h)
        async with Client(build_server(h.tmp, cfg), mode=mode) as c:
            await call(c, "init", run="run", spec="spec.json")
            await call(c, "brief", run="run", round=1, artefact="cand.md", control="ctrl.md")
            args = {"run": "run", "round": 1, "judges": [1]}
            with contextlib.suppress(Exception):
                await c.call_tool_mcp("judge", args, timeout=datetime.timedelta(seconds=0.5))
            retry = await c.call_tool_mcp("judge", args)
            assert retry.is_error and "being judged by another call" in retry.content[0].text  # type: ignore[union-attr]
            marker = h.run / "rounds" / "r1" / "running_j1.json"
            for _ in range(100):
                if not marker.exists():
                    break
                await asyncio.sleep(0.1)
        rows = results_by_judge(h.run / "rounds" / "r1")[1]
        assert [r["ok"] for r in rows] == [True]
