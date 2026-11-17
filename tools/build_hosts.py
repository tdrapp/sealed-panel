"""Write each host's copy of the agent skill from its one source, `skill/`.

    python tools/build_hosts.py            # rewrite the copies under hosts/
    python tools/build_hosts.py --check    # list the copies that drifted; write nothing

A host folder (`hosts/<host>/`) that holds `skills/<name>/` gets the files of `skill/` there,
byte for byte, `<name>` being the skill's `name`; a file the source does not have is removed.
A host folder that holds `agents/<judge>.md` keeps that file's front matter, which is the
host's own, and gets the judge instructions of `skill/judge.md` as its body. Host copies are
committed: a host installs from the repository as it is, so a copy is never built at install.
`tests/test_packaging.py` fails when a copy drifts.

A copy is real files: a host that fetches its folder alone does not fetch what a symbolic link
points to, so a link anywhere in a copy is drift, and the build refuses to write through one.
A folder under a host's `skills/` other than the skill's own copy is drift too, and is removed.
Only content is compared: a file's mode (its executable bit) is neither copied nor checked.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
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
    """Every host copy as it must read, and the files in a skill copy the source lacks.

    Args:
        root: The repository root, holding `skill/` and `hosts/`.

    Returns:
        The content each copied file must have, by path; and the stray entries to remove: files
        in a skill copy that the source lacks, and folders under `skills/` that are no copy.
    """
    source = root / "skill"
    name = skill_name(source)
    files = {p.name: p.read_bytes() for p in sorted(source.iterdir()) if p.is_file()}
    judge = (source / "judge.md").read_text(encoding="utf-8")
    want: dict[Path, bytes] = {}
    stray: list[Path] = []
    for host in sorted(p for p in (root / "hosts").iterdir() if p.is_dir()):
        if (host / "skills").is_dir():
            copy = host / "skills" / name
            want |= {copy / n: content for n, content in files.items()}
            stray += strays(host / "skills", name, files)
        agent = host / "agents" / JUDGE_AGENT
        if agent.is_file():
            text = judge_agent(agent.read_text(encoding="utf-8"), judge)
            want[agent] = text.encode("utf-8")
    return want, stray


def strays(skills: Path, name: str, files: dict[str, bytes]) -> list[Path]:
    """What a host's `skills/` folder holds that is not a copy of the source.

    Args:
        skills: A host's `skills/` folder.
        name: The skill's name, which names its copy there (which may not exist yet).
        files: The source's files, by name.

    Returns:
        The paths to remove: every other entry of `skills/`, and the entries of the copy that
        the source does not have.
    """
    found = [p for p in sorted(skills.iterdir()) if p.name != name]
    copy = skills / name
    if copy.is_dir():
        found += [p for p in sorted(copy.iterdir()) if p.name not in files]
    return found


def links(root: Path) -> list[Path]:
    """Every symbolic link a host's skill copy or judge agent would be read or written through.

    Args:
        root: The repository root.

    Returns:
        The links, sorted: a host folder, its `skills/` or `agents/` folder, or anything under
        them, file or folder.
    """
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
    """The host copies that differ from what `skill/` makes of them.

    Args:
        root: The repository root.

    Returns:
        One line per file to write or remove, relative to the root; empty when all agree.
    """
    want, stray = expected(root)
    lines = [f"symbolic link: {p.relative_to(root)}" for p in links(root)]
    lines += [
        f"differs: {p.relative_to(root)}"
        for p, content in want.items()
        if not p.is_file() or p.read_bytes() != content
    ]
    return lines + [f"not in skill/: {p.relative_to(root)}" for p in stray]


def build(root: Path) -> list[str]:
    """Write every host copy from `skill/`, and remove the files the source lacks.

    Args:
        root: The repository root.

    Returns:
        What was written or removed, as `drift` lists it before the write.

    Raises:
        ValueError: A copy goes through a symbolic link: writing would change what it points
            to, and the host would not fetch it anyway. Nothing is written.
    """
    found = links(root)
    if found:
        named = ", ".join(str(p.relative_to(root)) for p in found)
        raise ValueError(f"refusing to write through a symbolic link: {named}")
    changed = drift(root)
    want, stray = expected(root)
    for path, content in want.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_bytes() != content:
            path.write_bytes(content)
    for path in stray:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    return changed


def main(argv: list[str] | None = None) -> int:
    """Rewrite the host copies, or with `--check` report the drift and fail on any.

    Args:
        argv: The command-line arguments, without the program name.

    Returns:
        0 when the copies agree with `skill/` (or were rewritten), 1 when `--check` found drift,
        2 when the source or a copy is refused (a bad skill name, a symbolic link).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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
