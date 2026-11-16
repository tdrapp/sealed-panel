"""`tools/build_hosts.py`: each host folder's copy of the skill is written from `skill/`."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

TOOL = Path(__file__).resolve().parent.parent / "tools" / "build_hosts.py"
FRONT = "---\nname: eval-judge\ntools: []\n---\n\nold instructions\n"


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    if not TOOL.exists():  # the mutation check copies src, tests and examples only
        pytest.skip("not a checkout: the tools are not here")
    spec = importlib.util.spec_from_file_location("build_hosts", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\nsteps\n")
    (skill / "judge.md").write_text("# For people\n\n---\n\nJudge the items.\n")
    (tmp_path / "hosts" / "one" / "skills").mkdir(parents=True)
    (tmp_path / "hosts" / "one" / "agents").mkdir()
    (tmp_path / "hosts" / "one" / "agents" / "eval-judge.md").write_text(FRONT)
    (tmp_path / "hosts" / "two" / "skills" / "demo").mkdir(parents=True)
    (tmp_path / "hosts" / "two" / "skills" / "demo" / "old.md").write_text("gone\n")
    (tmp_path / "hosts" / "three").mkdir()  # no skills/ folder: gets no copy
    return tmp_path


def test_build_writes_every_copy_and_the_agent_body(tool: ModuleType, repo: Path) -> None:
    assert tool.drift(repo)
    tool.build(repo)
    assert tool.drift(repo) == []
    for host in ("one", "two"):
        copy = repo / "hosts" / host / "skills" / "demo"
        assert sorted(p.name for p in copy.iterdir()) == ["SKILL.md", "judge.md"]
        assert (copy / "SKILL.md").read_bytes() == (repo / "skill" / "SKILL.md").read_bytes()
    assert not (repo / "hosts" / "three" / "skills").exists()
    agent = (repo / "hosts" / "one" / "agents" / "eval-judge.md").read_text()
    assert agent == "---\nname: eval-judge\ntools: []\n---\n\nJudge the items.\n"


def test_drift_names_each_file_to_write_or_remove(tool: ModuleType, repo: Path) -> None:
    tool.build(repo)
    (repo / "hosts" / "two" / "skills" / "demo" / "SKILL.md").write_text("edited\n")
    (repo / "hosts" / "one" / "skills" / "demo" / "extra").mkdir()
    assert tool.drift(repo) == [
        "differs: hosts/two/skills/demo/SKILL.md",
        "not in skill/: hosts/one/skills/demo/extra",
    ]
    tool.build(repo)
    assert tool.drift(repo) == []


def test_a_malformed_source_is_refused(tool: ModuleType, repo: Path) -> None:
    (repo / "skill" / "judge.md").write_text("no separator\n")
    with pytest.raises(ValueError, match="no '---' line"):
        tool.build(repo)
    (repo / "skill" / "SKILL.md").write_text("---\ndescription: d\n---\n")
    with pytest.raises(ValueError, match="no name"):
        tool.drift(repo)


def test_an_agent_without_front_matter_is_refused(tool: ModuleType) -> None:
    with pytest.raises(ValueError, match="front matter"):
        tool.judge_agent("no front matter\n", "x\n---\ny\n")


def test_check_mode_fails_on_drift_and_writes_nothing(
    tool: ModuleType, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tool, "ROOT", repo)
    assert tool.main(["--check"]) == 1
    assert not (repo / "hosts" / "one" / "skills" / "demo").exists()
    assert tool.main([]) == 0
    assert tool.main(["--check"]) == 0
