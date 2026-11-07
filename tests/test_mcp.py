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
from sealed_panel.config import Config, load_config
from sealed_panel.errors import ConfigError
from sealed_panel.ledger import results_by_judge
from sealed_panel.mcp_server import JUDGE_TOOL_TIMEOUT_S, build_server
from sealed_panel.report import status as run_status_of
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
    # Outside the root: the server refuses a configuration its caller could rewrite.
    cfg = h.tmp.parent / f"{h.tmp.name}.judge.toml"
    cfg.write_text("[judge]\ncommand = [" + ", ".join(json.dumps(a) for a in argv) + "]\n")
    return cfg


def transcripts_of(h: Harness) -> Path:
    """A folder where a host would write transcripts: never under the root."""
    folder = h.tmp.parent / f"{h.tmp.name}-transcripts"
    folder.mkdir(exist_ok=True)
    return folder


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
        host = transcripts_of(h)
        async with Client(build_server(h.tmp, None, [host]), mode=mode) as c:
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
            t = host / "t.jsonl"
            brief = (h.run / "rounds" / "r1" / "brief_j2.md").read_text()
            row = {"type": "user", "agentId": "a3", "message": {"content": brief}}
            t.write_text(json.dumps(row) + "\n")
            v = await call(c, "void", run="run", round=1, judge=2, agent_id="a3", transcript=str(t))
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
        write_spec(h)
        async with Client(build_server(h.tmp), mode=mode) as c:
            await call(c, "init", run="run", spec="spec.json")
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
            for tool, args in (
                ("seal", {"artefact": "cand.md", "control": "ctrl.md"}),
                ("judge", {"sealed": True}),
                ("record", {"judge": 1, "agent_id": "a", "sealed": True, "reply": "{}"}),
                ("measure", {"id": "x", "value": "1", "sealed": True}),
                ("aggregate", {"sealed": True}),
                ("feedback", {"note": "a note"}),
            ):
                msg = await error(c, tool, run="run", **args)
                assert "differ from the configuration the server was started with" in msg, tool
            # Reading changes nothing: the read-only tools read it, and status says it is
            # not served.
            for tool, args in (
                ("rubrics_list", {}),
                ("rubrics_suggest", {"kind": "email"}),
                ("rubrics_show", {"id": "message"}),
                ("report", {}),
            ):
                await call(c, tool, run="run", **args)
            assert (await call(c, "status", run="run"))["served"] is False
        assert not (h.tmp / "pwned.txt").exists()

    async def test_a_server_without_a_judge_command_runs_no_judge(
        self, h: Harness, mode: str
    ) -> None:
        init_run(h.run, write_spec(h), load_config(runner_config(h)))
        async with Client(build_server(h.tmp), mode=mode) as c:
            msg = await error(c, "seal", run="run", artefact="cand.md", control="ctrl.md")
        assert "differ from the configuration" in msg

    async def test_a_runner_server_serves_no_host_mode_run(self, h: Harness, mode: str) -> None:
        init_run(h.run, write_spec(h))  # built beside the server, in host mode
        async with Client(build_server(h.tmp, runner_config(h)), mode=mode) as c:
            msg = await error(
                c, "record", run="run", judge=1, agent_id="a1", sealed=True, reply="{}"
            )
        assert "judge.command" in msg

    async def test_a_forged_grading_limit_with_a_recomputed_lock_is_refused(
        self, h: Harness, mode: str
    ) -> None:
        init_run(h.run, write_spec(h))
        data = json.loads((h.run / "config.json").read_text())
        data["limits"]["max_replacements"] = 5
        (h.run / "config.json").write_text(json.dumps(data))
        (h.run / "config.lock").write_text(Config(data).fingerprint + "\n")
        async with Client(build_server(h.tmp), mode=mode) as c:
            msg = await error(c, "seal", run="run", artefact="cand.md", control="ctrl.md")
            status = await call(c, "status", run="run")
        assert "limits.max_replacements" in msg
        assert status["served"] is False
        assert any(o.startswith("limits.max_replacements") for o in status["overrides"])

    def test_a_configuration_under_the_root_stops_the_server(self, h: Harness) -> None:
        inside = h.tmp / "srv.toml"
        inside.write_text("")
        with pytest.raises(ConfigError, match="under the root"):
            build_server(h.tmp, inside)

    async def test_the_configuration_is_read_once(self, h: Harness, mode: str) -> None:
        cfg = runner_config(h)
        write_spec(h)
        original = cfg.read_text()
        async with Client(build_server(h.tmp, cfg), mode=mode) as c:
            cfg.write_text('[judge]\ncommand = ["/bin/sh", "-c", "echo pwned"]\n')
            await call(c, "init", run="run", spec="spec.json")
            await call(c, "status", run="run")
        assert run_config_of(h.run).judge["command"][1] == str(STUB)
        assert "pwned" not in json.dumps(run_config_of(h.run).data)
        assert original != cfg.read_text()

    async def test_facts_file_stays_under_the_root(
        self, h: Harness, mode: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(h.tmp.parent))
        outside = h.tmp.parent / f"{h.tmp.name}.secret.txt"
        outside.write_text("TOP-SECRET-CANARY\n")
        (h.tmp / "facts.md").write_text("These facts sit under the root.\n")
        base = make_spec(judges_per_persona=2, max_rounds=1)
        cases = {"abs": str(outside), "up": f"../{outside.name}", "home": f"~/{outside.name}"}
        async with Client(build_server(h.tmp), mode=mode) as c:
            for name, facts in cases.items():
                (h.tmp / f"{name}.json").write_text(json.dumps({**base, "facts_file": facts}))
                msg = await error(c, "init", run=name, spec=f"{name}.json")
                assert "outside the root" in msg, name
                assert not (h.tmp / name / "spec.json").exists()
            (h.tmp / "ok.json").write_text(json.dumps({**base, "facts_file": "facts.md"}))
            await call(c, "init", run="fine", spec="ok.json")
        assert "under the root" in json.loads((h.tmp / "fine" / "spec.json").read_text())["facts"]


class TestTranscriptRoots:
    """A transcript is proof only under a --transcript-root, away from what the caller writes."""

    def judge_transcript(self, h: Harness, folder: Path, agent: str, judge: int = 1) -> Path:
        meta = next(m for m in h.keys(1)["judges"] if m["judge"] == judge)
        sc = {r: BAD for r in meta["key"].values()} | {"candidate": FLAT}
        raw = judge_json(meta, h.texts(1, meta), sc)
        brief = (h.run / "rounds" / "r1" / f"brief_j{judge}.md").read_text()
        rows = [
            {"type": "user", "agentId": agent, "message": {"content": brief}},
            {"type": "assistant", "agentId": agent,
             "message": {"content": [{"type": "text", "text": raw}]}},
        ]  # fmt: skip
        t = folder / f"{agent}.jsonl"
        t.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        return t

    async def setup_run(self, c: Client, h: Harness) -> None:
        write_spec(h)
        await call(c, "init", run="run", spec="spec.json")
        await call(c, "brief", run="run", round=1, artefact="cand.md", control="ctrl.md")

    async def test_inside_a_transcript_root_is_read(self, h: Harness, mode: str) -> None:
        host = transcripts_of(h)
        nested = host / "session" / "subagents"
        nested.mkdir(parents=True)
        async with Client(build_server(h.tmp, None, [host]), mode=mode) as c:
            await self.setup_run(c, h)
            t = self.judge_transcript(h, nested, "real")
            r = await call(
                c, "record", run="run", round=1, judge=1, agent_id="real", transcript=str(t)
            )
        assert r["status"] == "VALID" and r["source"] == "transcript"

    async def test_a_forged_transcript_under_the_root_is_no_proof(
        self, h: Harness, mode: str
    ) -> None:
        async with Client(build_server(h.tmp, None, [transcripts_of(h)]), mode=mode) as c:
            await self.setup_run(c, h)
            t = self.judge_transcript(h, h.tmp, "forged")
            for given in (str(t), "forged.jsonl"):
                msg = await error(
                    c, "record", run="run", round=1, judge=1, agent_id="forged", transcript=given
                )
                assert "outside every transcript root" in msg, given
                msg = await error(
                    c, "void", run="run", round=1, judge=1, agent_id="forged", transcript=given
                )
                assert "outside every transcript root" in msg, given
        assert results_by_judge(h.run / "rounds" / "r1") == {}

    async def test_without_a_root_no_transcript_is_taken(self, h: Harness, mode: str) -> None:
        async with Client(build_server(h.tmp), mode=mode) as c:
            await self.setup_run(c, h)
            t = self.judge_transcript(h, h.tmp, "forged")
            msg = await error(
                c, "record", run="run", round=1, judge=1, agent_id="forged", transcript=str(t)
            )
        assert "--transcript-root" in msg

    async def test_escapes_are_refused(self, h: Harness, mode: str) -> None:
        host = transcripts_of(h)
        beside = h.tmp.parent / f"{h.tmp.name}-beside"
        beside.mkdir()
        (host / "sub").mkdir()
        async with Client(build_server(h.tmp, None, [host]), mode=mode) as c:
            await self.setup_run(c, h)
            t = self.judge_transcript(h, beside, "esc")
            (host / "link.jsonl").symlink_to(t)
            (host / "dir").symlink_to(beside)
            (host / "same.jsonl").symlink_to(host / "sub")
            args = {"run": "run", "round": 1, "judge": 1, "agent_id": "esc"}
            msg = await error(
                c, "record", **args, transcript=f"{host}/sub/../../{beside.name}/esc.jsonl"
            )
            assert "outside every transcript root" in msg
            for given in (host / "link.jsonl", host / "dir" / "esc.jsonl", host / "same.jsonl"):
                msg = await error(c, "record", **args, transcript=str(given))
                assert "symbolic link" in msg, given

    @pytest.mark.parametrize("inside", [True, False], ids=["root inside", "transcripts inside"])
    async def test_an_overlap_turns_transcripts_off_and_says_why(
        self, h: Harness, mode: str, inside: bool
    ) -> None:
        """A host started from the home folder: the server still starts, without transcripts."""
        inner = h.tmp / "inner"
        inner.mkdir()
        root, transcripts = (inner, h.tmp) if inside else (h.tmp, inner)
        write_spec(h)
        (inner / "spec.json").write_text((h.tmp / "spec.json").read_text())
        async with Client(build_server(root, None, [transcripts]), mode=mode) as c:
            await call(c, "init", run="run", spec="spec.json")
            status = await call(c, "status", run="run")
            msg = await error(
                c, "record", run="run", round=1, judge=1, agent_id="a", transcript="x.jsonl"
            )
        assert status["transcripts"].startswith("off: the transcript root")
        assert "overlaps the root" in msg and "start the host from a project directory" in msg

    async def test_status_says_where_transcripts_are_read(self, h: Harness, mode: str) -> None:
        write_spec(h)
        host = transcripts_of(h)
        async with Client(build_server(h.tmp, None, [host]), mode=mode) as c:
            await call(c, "init", run="run", spec="spec.json")
            status = await call(c, "status", run="run")
        assert status["transcripts"] == f"read only under {host.resolve()}"
        async with Client(build_server(h.tmp), mode=mode) as c:
            assert "no --transcript-root" in (await call(c, "status", run="run"))["transcripts"]

    def test_an_unset_value_names_no_folder(self) -> None:
        assert mcp_server.usable_roots(["", "  ", "${user_config.transcript_root}"]) == []
        assert mcp_server.usable_roots(["/a/b"]) == [Path("/a/b")]

    @pytest.mark.parametrize("given", ["~/x/p", "$HOME/x/p", "${HOME}/x/p"])
    def test_the_home_is_expanded_however_the_host_passes_it(self, given: str) -> None:
        """The host passes a user_config default such as `~/...` unexpanded (measured)."""
        assert mcp_server.usable_roots([given], home="/home/u") == [Path("/home/u/x/p")]
        assert mcp_server.usable_roots(["~"], home="/home/u") == [Path("/home/u")]
        assert mcp_server.usable_roots(["$HOMEX/p"], home="/home/u") == []

    def test_a_candidate_id_is_quoted_in_the_commands(self) -> None:
        [promote, _] = mcp_server._decide_commands(Path("/lib"), "x; touch pwned #")
        assert promote.startswith("sealed-panel rubrics promote 'x; touch pwned #' ")


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


class TestPaths:
    """Every path a tool takes stays under the root (tests from the path probe)."""

    @pytest.fixture
    def outside(self, h: Harness, tmp_path_factory: pytest.TempPathFactory) -> Path:
        return tmp_path_factory.mktemp("outside")

    async def test_paths_outside_the_root_are_refused(
        self, h: Harness, outside: Path, mode: str
    ) -> None:
        write_spec(h)
        secret = outside / "secret.txt"
        secret.write_text("TOP-SECRET-LINE with enough words in it\n")
        link = h.tmp / "link"
        link.symlink_to(outside)
        async with Client(build_server(h.tmp), mode=mode) as c:
            for run in ("../elsewhere", str(outside / "run"), "link/run"):
                msg = await error(c, "init", run=run, spec="spec.json")
                assert "outside the root" in msg, run
            assert "never under ~" in await error(c, "init", run="~/run", spec="spec.json")
            assert not (outside / "run").exists() and not (h.tmp.parent / "elsewhere").exists()
            await call(c, "init", run="run", spec="spec.json")
            msg = await error(
                c, "brief", run="run", round=1, artefact=str(secret), control="ctrl.md"
            )
            assert "outside the root" in msg
            assert "outside the root" in await error(c, "init", run="r2", spec=str(secret))
            msg = await error(c, "rubrics_suggest", kind="email", text=str(secret))
            assert "outside the root" in msg
            msg = await error(c, "rubrics_list", library=str(outside))
            assert "outside the root" in msg
            await call(c, "status", run=str(h.run))  # an absolute path inside the root is fine

    async def test_a_foreign_directory_is_not_overwritten(self, h: Harness, mode: str) -> None:
        write_spec(h)
        victim = h.tmp / "someapp"
        victim.mkdir()
        (victim / "config.json").write_text('{"app": "precious"}')
        async with Client(build_server(h.tmp), mode=mode) as c:
            assert "not empty" in await error(c, "init", run="someapp", spec="spec.json")
        assert (victim / "config.json").read_text() == '{"app": "precious"}'
        assert sorted(p.name for p in victim.iterdir()) == ["config.json"]

    async def test_a_link_inside_a_run_is_refused(
        self, h: Harness, outside: Path, mode: str
    ) -> None:
        write_spec(h)
        async with Client(build_server(h.tmp), mode=mode) as c:
            await call(c, "init", run="run", spec="spec.json")
            (h.run / "rounds").mkdir()
            (h.run / "rounds" / "r1").symlink_to(outside)
            msg = await error(c, "brief", run="run", round=1, artefact="cand.md", control="ctrl.md")
        assert "symbolic link" in msg
        assert list(outside.iterdir()) == []

    async def test_a_library_inside_a_run_is_refused(self, h: Harness, mode: str) -> None:
        write_spec(h)
        async with Client(build_server(h.tmp), mode=mode) as c:
            await call(c, "init", run="run", spec="spec.json")
            await call(c, "brief", run="run", round=1, artefact="cand.md", control="ctrl.md")
            msg = await error(c, "rubrics_list", library="run/rounds/r1")
        assert "inside the run" in msg

    async def test_the_root_comes_from_the_flag_or_the_configuration(self, h: Harness) -> None:
        write_spec(h)
        inner = h.tmp / "inner"
        inner.mkdir()
        cfg = h.tmp.parent / f"{h.tmp.name}.root.toml"
        cfg.write_text(f'[mcp]\nroot = "{inner}"\n')
        spec = str(h.tmp / "spec.json")
        (inner / "spec.json").write_text((h.tmp / "spec.json").read_text())
        async with Client(build_server(None, cfg)) as c:
            assert "outside the root" in await error(c, "init", run="run", spec=spec)
            await call(c, "init", run="inside", spec="spec.json")
        assert (inner / "inside" / "rubric.lock").exists()
        async with Client(build_server(h.tmp, cfg)) as c:  # the flag wins
            await call(c, "init", run="run", spec=spec)
        assert not any(o.startswith("mcp.") for o in run_status_of(h.run)["overrides"])


class TestRunnerRefusalsThroughTools:
    """Every refusal of the runner holds through the tools (tests from the refusal probe)."""

    async def setup_run(self, c: Client, h: Harness) -> None:
        write_spec(h)
        await call(c, "init", run="run", spec="spec.json")
        await call(c, "brief", run="run", round=1, artefact="cand.md", control="ctrl.md")

    async def test_voids_are_capped_and_no_other_reply_gets_in(self, h: Harness, mode: str) -> None:
        async with Client(build_server(h.tmp, runner_config(h, "--exit", "3")), mode=mode) as c:
            await self.setup_run(c, h)
            args = {"run": "run", "round": 1, "judges": [1]}
            for _ in range(3):
                [seat] = (await call(c, "judge", **args))["seats"]
                assert seat["status"] == "VOID" and seat["exit_code"] == 3
            assert "voided attempts" in await error(c, "judge", **args)
            msg = await error(c, "record", run="run", round=1, judge=1, agent_id="x", reply="{}")
            assert "every reply comes from the runner" in msg
            t = h.tmp / "t.jsonl"
            t.write_text('{"type": "user", "agentId": "y", "message": {"content": "brief"}}\n')
            for tool in ("record", "void"):
                msg = await error(
                    c, tool, run="run", round=1, judge=1, agent_id="y", transcript="t.jsonl"
                )
                assert "--transcript-root" in msg
            assert "never fills a seat" in await error(c, "aggregate", run="run", round=1)

    async def test_a_filled_seat_takes_no_second_reply(self, h: Harness, mode: str) -> None:
        async with Client(build_server(h.tmp, runner_config(h)), mode=mode) as c:
            await self.setup_run(c, h)
            args = {"run": "run", "round": 1, "judges": [1]}
            [seat] = (await call(c, "judge", **args))["seats"]
            assert seat["status"] == "VALID"
            assert "already has a valid reply" in await error(c, "judge", **args)

    async def test_replacements_are_capped(self, h: Harness, mode: str) -> None:
        cfg = runner_config(h, "--low", "absent-word")
        async with Client(build_server(h.tmp, cfg), mode=mode) as c:
            await self.setup_run(c, h)
            args = {"run": "run", "round": 1, "judges": [1]}
            for _ in range(3):  # one attempt and two replacements
                [seat] = (await call(c, "judge", **args))["seats"]
                assert seat["status"] == "REJECTED" and "control scored" in seat["errors"][0]
            assert "rejected 3 times" in await error(c, "judge", **args)


class TestRoundThreeProbes:
    async def test_a_shadowing_key_never_reaches_a_tool(self, h: Harness, mode: str) -> None:
        write_spec(h)
        async with Client(build_server(h.tmp, None, [transcripts_of(h)]), mode=mode) as c:
            await call(c, "init", run="run", spec="spec.json")
            data = json.loads((h.run / "config.json").read_text())
            data["transcript"]["tool_block_type"] = "nothing"
            data["transcript.tool_block_type"] = "tool_use"
            (h.run / "config.json").write_text(json.dumps(data))
            (h.run / "config.lock").write_text(Config(data).fingerprint + "\n")
            for tool, args in (
                ("status", {}),
                ("report", {}),
                ("seal", {"artefact": "cand.md", "control": "ctrl.md"}),
            ):
                assert "holds a dot" in await error(c, tool, run="run", **args)

    async def test_a_run_from_an_older_version_is_served(self, h: Harness, mode: str) -> None:
        write_spec(h)
        async with Client(build_server(h.tmp), mode=mode) as c:
            await call(c, "init", run="run", spec="spec.json")
            data = json.loads((h.run / "config.json").read_text())
            del data["judge"]["max_stderr_bytes"]
            del data["mcp"]
            (h.run / "config.json").write_text(json.dumps(data))
            (h.run / "config.lock").write_text(Config(data).fingerprint + "\n")
            status = await call(c, "status", run="run")
            assert status["decision"] == "CONTINUE" and status["served"] is True
            await call(c, "seal", run="run", artefact="cand.md", control="ctrl.md")

    async def test_a_library_replaced_by_a_link_takes_no_feedback(
        self, h: Harness, mode: str
    ) -> None:
        cfg = runner_config(h, "--score", "5")
        write_spec(h)
        (h.tmp / "lib").mkdir()
        outside = h.tmp.parent / f"{h.tmp.name}-outside"
        outside.mkdir()
        async with Client(build_server(h.tmp, cfg), mode=mode) as c:
            await call(c, "init", run="run", spec="spec.json", library="lib")
            await call(c, "seal", run="run", artefact="cand.md", control="ctrl.md")
            await call(c, "judge", run="run", sealed=True)
            await call(c, "aggregate", run="run", sealed=True)
            (h.tmp / "lib").rename(h.tmp / "lib.real")
            (h.tmp / "lib").symlink_to(outside)
            msg = await error(c, "feedback", run="run", note="the evidence reads too low")
        assert "a link was put in its place" in msg
        assert list(outside.iterdir()) == []

    async def test_planted_stale_files_never_fail_a_judge_call(self, h: Harness, mode: str) -> None:
        cfg = runner_config(h)
        write_spec(h)
        async with Client(build_server(h.tmp, cfg), mode=mode) as c:
            await call(c, "init", run="run", spec="spec.json")
            await call(c, "seal", run="run", artefact="cand.md", control="ctrl.md")
            rdir = h.run / "sealed" / "r1"
            (rdir / "running_j999.json.x.stale").write_text("{}")
            seats = (await call(c, "judge", run="run", sealed=True, judges=[2]))["seats"]
        assert [s["status"] for s in seats] == ["VALID"]
