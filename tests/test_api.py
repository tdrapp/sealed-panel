"""The public API: the example of docs/api.md runs as written, and a Spec goes through the
same checks as a spec file."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

import sealed_panel
from conftest import make_spec
from sealed_panel.errors import EvalError
from sealed_panel.model import Spec
from sealed_panel.rubric import rubric_hash
from sealed_panel.run import load_frozen
from sealed_panel.steps import init_run

ROOT = Path(__file__).resolve().parent.parent
STUB = Path(__file__).resolve().parent / "stub_judge.py"
DRAFT = (
    "The remote build cache is ready for your services.\n\n"
    "Enable it on one service before 22 November and reply with its name.\n"
)
CONTROL = "We are pleased to share synergies that bring meaningful benefits to all teams.\n"


def example() -> str:
    text = (ROOT / "docs" / "api.md").read_text()
    found = re.search(r"```python\n(.*?)```", text, re.DOTALL)
    assert found, "docs/api.md has no python example"
    return found.group(1)


@pytest.mark.slow
def test_the_documented_example_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "draft.md").write_text(DRAFT)
    (tmp_path / "control.md").write_text(CONTROL)
    (tmp_path / "judge.toml").write_text(
        f"[judge]\ncommand = {json.dumps([sys.executable, str(STUB)])}\n"
    )
    code = example()
    lines = [ln for ln in code.splitlines() if ln.strip() and not ln.startswith("from ")]
    assert len(lines) <= 10  # ten lines, the imports aside
    exec(compile(code, "docs/api.md", "exec"), {})  # noqa: S102 (the repository's own example)
    report = capsys.readouterr().out
    assert report.startswith("# Evaluation: Build cache announcement")
    assert "SEALED" in report


def test_a_spec_built_in_python_freezes_like_its_file(tmp_path: Path) -> None:
    data = make_spec()
    spec = Spec(
        title=data["title"],
        purpose=data["purpose"],
        personas=tuple(data["personas"]),
        dimensions=tuple(data["dimensions"]),
        judges_per_persona=data["judges_per_persona"],
        max_rounds=data["max_rounds"],
    )
    assert spec.to_json() == data
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(data))
    from_file = init_run(tmp_path / "file", path)
    from_spec = init_run(tmp_path / "spec", spec)
    assert from_file == from_spec == rubric_hash(load_frozen(tmp_path / "spec"))


def test_a_spec_built_in_python_is_refused_like_a_file(tmp_path: Path) -> None:
    spec = Spec(title="t", purpose="p", personas=({"id": "a", "description": "A"},), dimensions=())
    with pytest.raises(EvalError, match=r"spec\.dimensions is missing or empty"):
        init_run(tmp_path / "run", spec)
    assert not (tmp_path / "run").exists()


def test_the_stable_names_import_from_the_package() -> None:
    assert sealed_panel.Spec is Spec
    for name in sealed_panel.__all__:
        assert hasattr(sealed_panel, name), name
    assert (Path(sealed_panel.__file__).parent / "py.typed").is_file()
