"""The test tiers: a test that starts a process other than git must be marked `slow`."""

from __future__ import annotations

import subprocess
import sys

import pytest


def test_an_unmarked_test_cannot_start_a_process() -> None:
    with pytest.raises(AssertionError, match=r"mark it @pytest\.mark\.slow"):
        subprocess.run([sys.executable, "-c", "pass"], check=True)


def test_an_unmarked_test_may_start_git() -> None:
    done = subprocess.run(["git", "--version"], capture_output=True, text=True, check=True)
    assert done.stdout.startswith("git version")


@pytest.mark.slow
def test_a_slow_test_may_start_a_process() -> None:
    assert subprocess.run([sys.executable, "-c", "pass"], check=True).returncode == 0
