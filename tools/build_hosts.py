"""Write each host's copy of the agent skill from its one source, `skill/`. Never deletes.

    python tools/build_hosts.py            # write the copies under hosts/
    python tools/build_hosts.py --check    # list what drifted; write nothing

A host folder (`hosts/<host>/`) that holds `skills/` gets the files of `skill/` in
`skills/<name>/`, byte for byte, `<name>` being the skill's `name`. A host folder that holds
`agents/eval-judge.md` keeps that file's front matter, which is the host's own, and gets the
judge instructions of `skill/judge.md` as its body. Each file is written whole and replaced at
once. Host copies are committed: a host installs from the repository as it is, so a copy is
never built at install. `tests/test_packaging.py` fails when a copy drifts.

The tool never deletes anything. Anything else under a host's `skills/` or `agents/`
(another folder, a stray or leftover temporary file, a file the source lacks inside the copy,
an entry named like a file of the copy that is not a regular file, tracked or not) is an
orphan: `--check` reports it, and the build refuses and names it, for you to remove by hand.
A copy is real files: a host that fetches its folder alone does not fetch what a symbolic link
points to, so a link on the way to a copy (`hosts/` itself, a host folder, its `skills/` or
`agents/`, or anything under them) is drift, and the build refuses to write through one. Only
content is compared: a file's mode (its executable bit) is neither copied nor checked.

Exit codes: with `--check`, 0 when every copy agrees and 1 on any drift, links and orphans
included; without it, 0 when the copies were written (or already agreed) and 2 when the build
is refused, nothing written. Either way, 2 when `skill/` itself is refused: no `name`, a name
not made of a-z, 0-9 and -, or a `judge.md` without its `---` line.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The judge agent's file name in a host's `agents/` folder.
JUDGE_AGENT = "eval-judge.md"
# `skill/judge.md` puts its preamble for people before this line, the instructions after it.
SEPARATOR = "\n---\n"
# A skill name names a folder under hosts/: no path separator, no dot, nothing else.
NAME = re.compile(r"[a-z0-9-]+")


def skill_name(source: Path) -> str:
    """The skill's `name`, from the front matter of its `SKILL.md`.

    Args:
        source: The skill's source folder.

    Returns:
        The name a host reads, which names the skill's folder in a host copy.

    Raises:
        ValueError: `SKILL.md` has no `name` in its front matter, or one that is not made of
            `a-z`, `0-9` and `-` only.
    """
    front = (source / "SKILL.md").read_text(encoding="utf-8").split("---\n")[1]
    found = re.search(r"^name: (\S+)$", front, re.MULTILINE)
    if not found:
        raise ValueError(f"{source / 'SKILL.md'} has no name in its front matter")
    if not NAME.fullmatch(found[1]):
        raise ValueError(f"the skill name {found[1]!r} is not made of a-z, 0-9 and - only")
    return found[1]


def judge_agent(current: str, judge: str) -> str:
    """A host's judge agent file: its own front matter, the judge instructions as its body.

    Args:
        current: The agent file as committed, whose front matter is kept.
        judge: The text of `skill/judge.md`.

    Returns:
        The agent file as it must read.

    Raises:
        ValueError: The agent file has no front matter, or `judge.md` no separator.
    """
    if not current.startswith("---\n") or current.count("---\n") < 2:
        raise ValueError("the judge agent file has no front matter")
    if SEPARATOR not in judge:
        raise ValueError("skill/judge.md has no '---' line before the judge instructions")
    front = current.split("---\n", 2)[1]
    instructions = judge.split(SEPARATOR, 1)[1].strip()
    return f"---\n{front}---\n\n{instructions}\n"


def expected(root: Path) -> tuple[dict[Path, bytes], list[Path]]:
    """Every host copy as it must read, and what stands in the way of writing it.

    Args:
        root: The repository root, holding `skill/` and `hosts/`.

    Returns:
        The content each copied file must have, by path; and the orphans: what a host's
        `skills/` holds that is not a file of the copy (another entry of `skills/`, a file the
        source lacks inside the copy or one that is not a regular file, or the copy's own name
        taken by a file), and what its `agents/` holds besides the judge agent's file.
    """
    source = root / "skill"
    name = skill_name(source)
    files = {p.name: p.read_bytes() for p in sorted(source.iterdir()) if p.is_file()}
    judge = (source / "judge.md").read_text(encoding="utf-8")
    want: dict[Path, bytes] = {}
    orphan: list[Path] = []
    for host in sorted(p for p in (root / "hosts").iterdir() if p.is_dir()):
        if (host / "skills").is_dir():
            copy = host / "skills" / name
            want |= {copy / n: content for n, content in files.items()}
            orphan += orphans(host / "skills", name, files)
        orphan += agent_orphans(host / "agents")
        agent = host / "agents" / JUDGE_AGENT
        if agent.is_file():
            text = judge_agent(agent.read_text(encoding="utf-8"), judge)
            want[agent] = text.encode("utf-8")
    return want, orphan


def orphans(skills: Path, name: str, files: dict[str, bytes]) -> list[Path]:
    """What a host's `skills/` folder holds that is not a file of the skill's copy.

    Args:
        skills: A host's `skills/` folder.
        name: The skill's name, which names its copy there (which may not exist yet).
        files: The source's files, by name.

    Returns:
        Every other entry of `skills/`, file or folder; the copy itself when a file holds its
        name; and the entries of the copy that the source does not have, or that carry a file's
        name without being a regular file (a folder named `SKILL.md`).
    """
    found = [p for p in sorted(skills.iterdir()) if p.name != name]
    copy = skills / name
    if copy.is_dir():
        found += [p for p in sorted(copy.iterdir()) if p.name not in files or not p.is_file()]
    elif copy.exists() or copy.is_symlink():
        found.append(copy)
    return found


def agent_orphans(agents: Path) -> list[Path]:
    """What a host's `agents/` folder holds that the build does not write.

    Args:
        agents: A host's `agents/` folder, which may not exist.

    Returns:
        Every entry but the judge agent's file (a leftover temporary file, any other file or
        folder), and that one too when it is not a regular file.
    """
    if not agents.is_dir():
        return []
    return [p for p in sorted(agents.iterdir()) if p.name != JUDGE_AGENT or not p.is_file()]


def links(root: Path) -> list[Path]:
    """Every symbolic link a host's skill copy or judge agent would be read or written through.

    Args:
        root: The repository root.

    Returns:
        The links, sorted: `hosts/` itself, a host folder, its `skills/` or `agents/` folder,
        or anything under them, file or folder.
    """
    if (root / "hosts").is_symlink():
        return [root / "hosts"]
    found: list[Path] = []
    for host in sorted((root / "hosts").iterdir()):
        if host.is_symlink():
            found.append(host)
            continue
        for top in (host / "skills", host / "agents"):
            found += links_under(top)
    return sorted(found)


def links_under(top: Path) -> list[Path]:
    """The folder itself if it is a link, else every link below it, without following one.

    Args:
        top: A host's `skills/` or `agents/` folder, which may not exist.

    Returns:
        The links found.
    """
    if top.is_symlink():
        return [top]
    found: list[Path] = []
    for folder, dirs, names in os.walk(top):  # os.walk does not descend into a linked folder
        found += [Path(folder) / n for n in dirs + names if (Path(folder) / n).is_symlink()]
    return found


def drift(root: Path) -> list[str]:
    """Everything that keeps the host copies from agreeing with `skill/`.

    Args:
        root: The repository root.

    Returns:
        One line per link, file to write or orphan, relative to the root; empty when all agree.
    """
    found = links(root)
    lines = [f"symbolic link: {p.relative_to(root)}" for p in found]
    if found:  # reading on would follow the links
        return lines
    want, orphan = expected(root)
    lines += [
        f"differs: {p.relative_to(root)}"
        for p, content in want.items()
        if not p.is_file() or p.read_bytes() != content
    ]
    return lines + [f"orphan, remove it by hand: {p.relative_to(root)}" for p in orphan]


def build(root: Path) -> list[str]:
    """Write every host copy from `skill/`, file by file; never delete anything.

    Args:
        root: The repository root.

    Returns:
        The files written, relative to the root.

    Raises:
        ValueError: A copy goes through a symbolic link (writing would change what it points
            to, and the host would not fetch it anyway), or an orphan sits under a host's
            `skills/`, to be removed by hand. Nothing is written.
    """
    found = links(root)
    if found:
        named = ", ".join(str(p.relative_to(root)) for p in found)
        raise ValueError(f"refusing to write through a symbolic link: {named}")
    want, orphan = expected(root)
    if orphan:
        named = ", ".join(str(p.relative_to(root)) for p in orphan)
        raise ValueError(f"refusing to build beside an orphan; remove it by hand: {named}")
    written = []
    for path, content in want.items():
        if not path.is_file() or path.read_bytes() != content:
            replace(path, content)
            written.append(f"wrote: {path.relative_to(root)}")
    return written


def replace(path: Path, content: bytes) -> None:
    """Write a file whole, then put it in place at once (a reader sees the old or the new).

    Args:
        path: The file to write; its folder is created when missing.
        content: Its bytes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as f:
        f.write(content)
    os.chmod(f.name, 0o644)  # a temporary file is created 0600; a copy is read by the host
    os.replace(f.name, path)


def main(argv: list[str] | None = None) -> int:
    """Write the host copies, or with `--check` report the drift. Never deletes anything.

    Args:
        argv: The command-line arguments, without the program name.

    Returns:
        With `--check`: 0 when every copy agrees, 1 on any drift (links and orphans included).
        Without it: 0 when the copies were written or already agreed, 2 when the build is
        refused (a link, an orphan to remove by hand), nothing written. Either way, 2 when
        `skill/` itself is refused (no name, a bad name, a `judge.md` without its separator).
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check", action="store_true", help="report drift, write nothing")
    args = parser.parse_args(argv)
    try:
        lines = drift(ROOT) if args.check else build(ROOT)
    except ValueError as refused:
        sys.stderr.write(f"build_hosts: {refused}\n")
        return 2
    sys.stdout.write("".join(f"{line}\n" for line in lines))
    return 1 if args.check and lines else 0


if __name__ == "__main__":
    sys.exit(main())
