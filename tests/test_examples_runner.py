"""The runner examples: the judge template behind the runner, and the library usage."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from conftest import Harness, make_spec
from sealed_panel.aggregate import aggregate_round
from sealed_panel.config import load_config
from sealed_panel.report import render_report
from sealed_panel.runner import judge_round
from sealed_panel.spec import init_run

# Every test here starts a judge command or a model endpoint.
pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "examples" / "runner" / "judge_template.py"
USAGE = ROOT / "examples" / "runner" / "library_usage.py"
STUB = Path(__file__).resolve().parent / "stub_judge.py"
ENV = ["JUDGE_MODEL_COMMAND", "JUDGE_MODEL_URL", "JUDGE_MODEL_KEY"]


def template_config(h: Harness) -> Path:
    cfg = h.tmp / "judge.toml"
    cfg.write_text(
        "[judge]\n"
        f"command = {json.dumps([sys.executable, str(TEMPLATE)])}\n"
        f"env_allow = {json.dumps(ENV)}\n"
    )
    return cfg


def spec_file(h: Harness) -> Path:
    sp = h.tmp / "spec.json"
    sp.write_text(json.dumps(make_spec(judges_per_persona=2, max_rounds=1)))
    return sp


@pytest.fixture
def model_command(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JUDGE_MODEL_COMMAND", json.dumps([sys.executable, str(STUB)]))


def test_the_template_judges_through_a_model_command(h: Harness, model_command: None) -> None:
    init_run(h.run, spec_file(h), load_config(template_config(h)))
    h.brief(1, "cand")
    results = judge_round(h.run, 1, False)
    assert [r["ok"] for r in results] == [True, True]


def test_the_template_without_a_model_is_a_void(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    init_run(h.run, spec_file(h), load_config(template_config(h)))
    h.brief(1, "cand")
    r = judge_round(h.run, 1, False, [1])[0]
    assert r["void"] and r["errors"] == ["the judge command exited with status 2"]
    log = (h.run / "rounds" / "r1" / r["runner"]["stderr_log"]).read_text()
    assert "name the model: -- MODEL ARGV... or --url URL" in log


@pytest.fixture
def endpoint() -> Iterator[tuple[str, list[dict[str, Any]]]]:
    """An HTTP endpoint that plays the model with the stub judge, and the requests it saw."""
    seen: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"body": body, "auth": self.headers.get("Authorization")})
            text = subprocess.run(
                [sys.executable, str(STUB)],
                input=body["prompt"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            out = json.dumps({"text": text}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/judge", seen
    server.shutdown()


def test_the_template_judges_through_an_http_endpoint(
    h: Harness, monkeypatch: pytest.MonkeyPatch, endpoint: tuple[str, list[dict[str, Any]]]
) -> None:
    url, seen = endpoint
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JUDGE_MODEL_URL", url)
    monkeypatch.setenv("JUDGE_MODEL_KEY", "test-key")
    init_run(h.run, spec_file(h), load_config(template_config(h)))
    h.brief(1, "cand")
    assert [r["ok"] for r in judge_round(h.run, 1, False)] == [True, True]
    brief = (h.run / "rounds" / "r1" / "brief_j1.md").read_text()
    assert seen[0]["body"]["prompt"] == brief
    assert seen[0]["auth"] == "Bearer test-key"


def test_the_template_sends_the_judge_instructions() -> None:
    sys.path.insert(0, str(TEMPLATE.parent))
    try:
        import judge_template
    finally:
        sys.path.remove(str(TEMPLATE.parent))
    judge = ROOT / "skill" / "judge.md"
    if not judge.exists():  # the mutation check copies src, tests and examples only
        pytest.skip("not a checkout")
    instructions = judge.read_text(encoding="utf-8").split("\n---\n", 1)[1].strip()
    assert instructions == judge_template.SYSTEM


def test_the_library_example_grades_once(
    h: Harness, model_command: None, capsys: pytest.CaptureFixture[str]
) -> None:
    sys.path.insert(0, str(USAGE.parent))
    try:
        import library_usage
    finally:
        sys.path.remove(str(USAGE.parent))
    args = ["--spec", spec_file(h), "--config", template_config(h)]
    args += ["--artefact", h.files["cand"], "--control", h.files["ctrl"]]
    assert library_usage.main([str(h.run), *map(str, args)]) == 0
    assert "Grade (sealed panel): clarity 4 (4/4); evidence 4 (4/4)" in capsys.readouterr().out
    assert library_usage.main([str(h.run), *map(str, args)]) == 2
    assert "already frozen" in capsys.readouterr().err


@pytest.fixture
def failing_endpoint() -> Iterator[tuple[str, list[tuple[int, bytes]], list[str]]]:
    """An HTTP endpoint that answers what the test queues, (status, body) per request, and the
    paths it was called on."""
    queue: list[tuple[int, bytes]] = []
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            hits.append(self.path)
            status, out = queue[0]
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/judge", queue, hits
    server.shutdown()


@pytest.mark.parametrize(
    ("status", "body", "logged"),
    [
        (429, b'{"error": "rate limited"}', "HTTP Error 429"),
        (500, b'{"error": "internal"}', "HTTP Error 500"),
        (200, b"<html>not json</html>", "Expecting value"),
        (200, b'{"answer": "no text field"}', "has no text field"),
        (200, b'{"text": {"items": {}}}', "has no text field"),
    ],
    ids=["429", "500", "not-json", "no-text", "text-not-a-string"],
)
def test_an_http_failure_is_a_void_never_a_reply(
    h: Harness,
    monkeypatch: pytest.MonkeyPatch,
    failing_endpoint: tuple[str, list[tuple[int, bytes]], list[str]],
    status: int,
    body: bytes,
    logged: str,
) -> None:
    url, queue, hits = failing_endpoint
    queue.append((status, body))
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JUDGE_MODEL_URL", url)
    init_run(h.run, spec_file(h), load_config(template_config(h)))
    h.brief(1, "cand")
    r = judge_round(h.run, 1, False, [1])[0]
    assert r["void"] and not r["ok"] and r["items"] == {}
    assert r["errors"] == ["the judge command exited with status 2"]
    assert hits == ["/judge"], "one call: asking again would be a re-roll"
    log = (h.run / "rounds" / "r1" / r["runner"]["stderr_log"]).read_text()
    assert logged in log


def test_the_shipped_configuration_names_the_model_in_the_command() -> None:
    cfg = load_config(ROOT / "examples" / "runner" / "judge.toml")
    command = cfg.judge["command"]
    assert "--" in command and command[command.index("--") + 1 :]
    assert cfg.judge["env_allow"] == ["JUDGE_MODEL_KEY"]  # a credential only, never the model
    assert cfg.judge["model_id"]


def test_the_template_takes_the_model_on_its_command_line(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    cfg = h.tmp / "argv.toml"
    command = [sys.executable, str(TEMPLATE), "--", sys.executable, str(STUB)]
    cfg.write_text(f"[judge]\ncommand = {json.dumps(command)}\n")
    init_run(h.run, spec_file(h), load_config(cfg))
    h.brief(1, "cand")
    assert [r["ok"] for r in judge_round(h.run, 1, False)] == [True, True]
    report = render_report_after(h)
    assert "ran under different values" not in report


def render_report_after(h: Harness) -> str:
    aggregate_round(h.run, 1, False)
    return render_report(h.run)


def test_a_model_changed_through_the_environment_is_flagged(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's scenario: the model lives in an allowed variable, a first attempt is
    rejected, the variable is changed, and the replacement judges under the other model."""
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    first = json.dumps([sys.executable, str(STUB), "--low", "no-such-word"])
    monkeypatch.setenv("JUDGE_MODEL_COMMAND", first)
    init_run(h.run, spec_file(h), load_config(template_config(h)))
    h.brief(1, "cand")
    rejected = judge_round(h.run, 1, False, [1])[0]
    assert not rejected["ok"] and not rejected.get("void")  # the control scored too high
    monkeypatch.setenv("JUDGE_MODEL_COMMAND", json.dumps([sys.executable, str(STUB)]))
    assert [r["ok"] for r in judge_round(h.run, 1, False)] == [True, True]
    entry = aggregate_round(h.run, 1, False)
    assert entry["runner_env_changed"] == ["JUDGE_MODEL_COMMAND"]
    report = render_report(h.run)
    assert "ran under different values of JUDGE_MODEL_COMMAND across attempts" in report
    stored = "".join(p.read_text() for p in (h.run / "rounds" / "r1").glob("*.json"))
    assert "no-such-word" not in stored  # a hash of the value, never the value
    assert hashlib.sha256(first.encode()).hexdigest() in stored


def test_one_model_through_the_environment_is_not_flagged(h: Harness, model_command: None) -> None:
    init_run(h.run, spec_file(h), load_config(template_config(h)))
    h.brief(1, "cand")
    judge_round(h.run, 1, False)
    assert aggregate_round(h.run, 1, False)["runner_env_changed"] == []
