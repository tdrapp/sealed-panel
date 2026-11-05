"""A judge that leaves a process holding its stdout: it starts a grandchild in a session of its
own (out of reach of the runner's process group), writes the grandchild's pid to stderr, and
exits 0 at once without a reply."""

from __future__ import annotations

import subprocess
import sys

sys.stdin.buffer.read()
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
)
print(f"GRANDCHILD {child.pid}", file=sys.stderr, flush=True)
