"""Commit message check, run as the commit-msg hook: a Conventional Commits subject under 72.

    python tools/check_commit_msg.py .git/COMMIT_EDITMSG

The subject is the first line: `type: summary` or `type(scope): summary`, with a type from
TYPES, an optional `!` for a breaking change, and fewer than MAX_SUBJECT characters. A
`fixup!`, `squash!` or `amend!` prefix (an autosquash commit) is read past. Comment lines are
ignored, as git strips them.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TYPES = ("feat", "fix", "docs", "test", "ci", "build", "chore", "refactor", "style", "perf")
# The subject is under 72 characters.
MAX_SUBJECT = 71
SUBJECT = re.compile(r"(?:" + "|".join(TYPES) + r")(?:\([a-z0-9][a-z0-9._-]*\))?!?: \S.*")
AUTOSQUASH = re.compile(r"(?:fixup|squash|amend)! ")


def problems(message: str) -> list[str]:
    """Why a commit message breaks the rule; empty when it follows it.

    Args:
        message: The commit message, as git hands it to the hook.

    Returns:
        One line per problem.
    """
    lines = [line for line in message.splitlines() if not line.startswith("#")]
    subject = lines[0].rstrip() if lines else ""
    while AUTOSQUASH.match(subject):
        subject = AUTOSQUASH.sub("", subject, count=1)
    found = []
    if not SUBJECT.fullmatch(subject):
        found.append(
            f"the subject {subject!r} is not `type: summary` with a type from: {', '.join(TYPES)}"
        )
    if len(subject) > MAX_SUBJECT:
        found.append(f"the subject has {len(subject)} characters; keep it under 72")
    if len(lines) > 1 and lines[1].strip():
        found.append("leave a blank line between the subject and the body")
    return found


def main(argv: list[str] | None = None) -> int:
    """Check the message file git passes to the hook.

    Args:
        argv: The command-line arguments: the message file.

    Returns:
        0 when the message follows the rule, 1 otherwise.
    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__)
        return 2
    found = problems(Path(args[0]).read_text(encoding="utf-8"))
    for line in found:
        print(f"commit message: {line}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
