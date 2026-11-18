"""`tools/build_hosts.py`: each host folder's copy of the skill is written from `skill/`, and
nothing is ever deleted: what stands in the way is refused, for a person to remove."""

from __future__ import annotations

import importlib.util
import re
import shutil
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
    root = tmp_path / "repo"
    skill = root / "skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\nsteps\n")
    (skill / "judge.md").write_text("# For people\n\n---\n\nJudge the items.\n")
    (root / "hosts" / "one" / "skills").mkdir(parents=True)
    (root / "hosts" / "one" / "agents").mkdir()
    (root / "hosts" / "one" / "agents" / "eval-judge.md").write_text(FRONT)
    (root / "hosts" / "two" / "skills" / "demo").mkdir(parents=True)
    (root / "hosts" / "three").mkdir()  # no skills/ folder: gets no copy
    return root


def snapshot(folder: Path) -> dict[str, bytes | None]:
    """Every path under a folder with its bytes (None for a folder), links not followed."""
    return {
        str(p.relative_to(folder)): (p.read_bytes() if p.is_file() else None)
        for p in sorted(folder.rglob("*"))
    }


def edit_source(repo: Path) -> None:
    """Change the source, so that a build would have something to write."""
    (repo / "skill" / "SKILL.md").write_text("---\nname: demo\ndescription: e\n---\nnew\n")


def test_build_writes_every_copy_and_the_agent_body(tool: ModuleType, repo: Path) -> None:
    assert tool.drift(repo)
    written = tool.build(repo)
    assert "wrote: hosts/one/skills/demo/SKILL.md" in written
    assert tool.drift(repo) == [] and tool.build(repo) == []
    for host in ("one", "two"):
        copy = repo / "hosts" / host / "skills" / "demo"
        assert sorted(p.name for p in copy.iterdir()) == ["SKILL.md", "judge.md"]
        assert (copy / "SKILL.md").read_bytes() == (repo / "skill" / "SKILL.md").read_bytes()
        assert (copy / "SKILL.md").stat().st_mode & 0o777 == 0o644
    assert not (repo / "hosts" / "three" / "skills").exists()
    agent = (repo / "hosts" / "one" / "agents" / "eval-judge.md").read_text()
    assert agent == "---\nname: eval-judge\ntools: []\n---\n\nJudge the items.\n"


def test_an_extra_file_in_a_copy_is_an_orphan_left_in_place(tool: ModuleType, repo: Path) -> None:
    tool.build(repo)
    (repo / "hosts" / "two" / "skills" / "demo" / "SKILL.md").write_text("edited\n")
    extra = repo / "hosts" / "one" / "skills" / "demo" / "extra.md"
    extra.write_text("mine\n")
    assert tool.drift(repo) == [
        "differs: hosts/two/skills/demo/SKILL.md",
        "orphan, remove it by hand: hosts/one/skills/demo/extra.md",
    ]
    before = snapshot(repo)
    with pytest.raises(ValueError, match=r"remove it by hand: hosts/one/skills/demo/extra\.md"):
        tool.build(repo)
    assert snapshot(repo) == before  # nothing written, nothing removed
    extra.unlink()  # by hand, as the refusal says
    tool.build(repo)
    assert tool.drift(repo) == []


def test_other_entries_under_skills_are_orphans_left_in_place(tool: ModuleType, repo: Path) -> None:
    tool.build(repo)
    draft = repo / "hosts" / "one" / "skills" / "draft"
    draft.mkdir()
    (draft / "SKILL.md").write_text("---\nname: draft\n---\n")
    (repo / "hosts" / "one" / "skills" / ".DS_Store").write_bytes(b"\x00\x01")
    edit_source(repo)
    assert tool.drift(repo)[-2:] == [
        "orphan, remove it by hand: hosts/one/skills/.DS_Store",
        "orphan, remove it by hand: hosts/one/skills/draft",
    ]
    before = snapshot(repo)
    with pytest.raises(ValueError, match=r"hosts/one/skills/\.DS_Store, hosts/one/skills/draft"):
        tool.build(repo)
    assert snapshot(repo) == before


def test_a_file_holding_the_copys_name_is_refused_cleanly(
    tool: ModuleType, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy = repo / "hosts" / "two" / "skills" / "demo"
    copy.rmdir()
    copy.write_text("not a folder\n")
    assert "orphan, remove it by hand: hosts/two/skills/demo" in tool.drift(repo)
    with pytest.raises(ValueError, match="remove it by hand: hosts/two/skills/demo"):
        tool.build(repo)
    monkeypatch.setattr(tool, "ROOT", repo)
    assert tool.main(["--check"]) == 1
    assert tool.main([]) == 2
    assert copy.read_text() == "not a folder\n"


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


def test_the_exit_codes(tool: ModuleType, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool, "ROOT", repo)
    assert tool.main(["--check"]) == 1  # drift
    assert not (repo / "hosts" / "one" / "skills" / "demo").exists()  # --check writes nothing
    assert tool.main([]) == 0  # written
    assert tool.main(["--check"]) == 0  # clean
    (repo / "hosts" / "one" / "skills" / "draft").mkdir()
    assert tool.main(["--check"]) == 1 and tool.main([]) == 2  # an orphan: drift, refused


def test_a_copy_replaced_by_a_link_is_drift_and_never_written_through(
    tool: ModuleType, repo: Path
) -> None:
    tool.build(repo)
    copy = repo / "hosts" / "two" / "skills" / "demo"
    shutil.rmtree(copy)
    copy.symlink_to(repo / "skill", target_is_directory=True)  # same bytes, through a link
    assert tool.drift(repo) == ["symbolic link: hosts/two/skills/demo"]
    edit_source(repo)
    before = snapshot(repo)
    with pytest.raises(ValueError, match="symbolic link: hosts/two/skills/demo"):
        tool.build(repo)
    assert snapshot(repo) == before


def test_a_linked_file_in_a_copy_or_a_linked_agent_is_drift(tool: ModuleType, repo: Path) -> None:
    tool.build(repo)
    linked = repo / "hosts" / "one" / "skills" / "demo" / "judge.md"
    linked.unlink()
    linked.symlink_to(repo / "skill" / "judge.md")
    agent = repo / "hosts" / "one" / "agents" / "eval-judge.md"
    target = repo / "agent.md"
    target.write_bytes(agent.read_bytes())
    agent.unlink()
    agent.symlink_to(target)
    assert tool.drift(repo) == [
        "symbolic link: hosts/one/agents/eval-judge.md",
        "symbolic link: hosts/one/skills/demo/judge.md",
    ]


@pytest.mark.parametrize(
    "linked", ["hosts", "hosts/one", "hosts/one/skills"], ids=["hosts", "host", "skills"]
)
def test_a_linked_folder_on_the_way_is_refused_and_its_target_untouched(
    tool: ModuleType, repo: Path, monkeypatch: pytest.MonkeyPatch, linked: str
) -> None:
    tool.build(repo)
    edit_source(repo)
    folder = repo / linked
    target = repo.parent / "elsewhere"
    shutil.copytree(folder, target, symlinks=True)
    (target / "marker").write_text("keep me\n")
    shutil.rmtree(folder)
    folder.symlink_to(target, target_is_directory=True)
    before = snapshot(target)
    assert tool.drift(repo) == [f"symbolic link: {linked}"]
    with pytest.raises(ValueError, match=f"symbolic link: {re.escape(linked)}"):
        tool.build(repo)
    monkeypatch.setattr(tool, "ROOT", repo)
    assert tool.main(["--check"]) == 1 and tool.main([]) == 2
    assert snapshot(target) == before  # the marker is intact and nothing new was written


@pytest.mark.parametrize("name", ["../escape", "a/b", "Demo", "x.y"])
def test_a_skill_name_that_is_not_a_plain_folder_name_is_refused(
    tool: ModuleType, repo: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    (repo / "skill" / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n")
    with pytest.raises(ValueError, match="not made of a-z"):
        tool.build(repo)
    monkeypatch.setattr(tool, "ROOT", repo)
    assert tool.main([]) == 2
    assert list((repo / "hosts" / "one" / "skills").iterdir()) == []


def test_the_tool_has_no_code_that_deletes() -> None:
    if not TOOL.exists():
        pytest.skip("not a checkout: the tools are not here")
    code = TOOL.read_text(encoding="utf-8")
    calls = re.findall(r"\b(?:unlink|rmtree|rmdir|remove|removedirs)\(", code)
    assert calls == [], calls


def test_help_states_what_it_writes_that_it_never_deletes_and_the_codes(
    tool: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        tool.main(["--help"])
    out = capsys.readouterr().out
    assert len(out.splitlines()) > 10
    for phrase in ("Never deletes", "never deletes anything", "remove by hand", "Exit codes"):
        assert phrase in out, phrase


def test_a_folder_named_like_a_file_of_the_copy_is_an_orphan_refused_before_any_write(
    tool: ModuleType, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool.build(repo)
    # Hosts are processed in name order: "two" comes last, after "one" would have been written.
    planted = repo / "hosts" / "two" / "skills" / "demo" / "SKILL.md"
    planted.unlink()
    planted.mkdir()
    edit_source(repo)
    assert "orphan, remove it by hand: hosts/two/skills/demo/SKILL.md" in tool.drift(repo)
    before = snapshot(repo / "hosts")
    monkeypatch.setattr(tool, "ROOT", repo)
    assert tool.main(["--check"]) == 1
    assert tool.main([]) == 2
    assert snapshot(repo / "hosts") == before  # no host written, no temporary file left


@pytest.mark.parametrize("entry", [".eval-judge.md.k3x9q", "other.md", "folder"])
def test_any_other_entry_of_agents_is_an_orphan(
    tool: ModuleType, repo: Path, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    tool.build(repo)
    stray = repo / "hosts" / "one" / "agents" / entry
    if entry == "folder":
        stray.mkdir()
    else:
        stray.write_text("left over\n")
    edit_source(repo)
    assert tool.drift(repo)[-1] == f"orphan, remove it by hand: hosts/one/agents/{entry}"
    before = snapshot(repo / "hosts")
    monkeypatch.setattr(tool, "ROOT", repo)
    assert tool.main(["--check"]) == 1 and tool.main([]) == 2
    assert snapshot(repo / "hosts") == before


def test_a_judge_agent_that_is_not_a_regular_file_is_an_orphan(
    tool: ModuleType, repo: Path
) -> None:
    agent = repo / "hosts" / "one" / "agents" / "eval-judge.md"
    agent.unlink()
    agent.mkdir()
    assert "orphan, remove it by hand: hosts/one/agents/eval-judge.md" in tool.drift(repo)
    with pytest.raises(ValueError, match=r"hosts/one/agents/eval-judge\.md"):
        tool.build(repo)
