"""Smoke check of an installed MCP server: it starts and answers `initialize` over stdio.

The build job installs the wheel in a fresh environment and runs this script against the
`sealed-panel` command of that environment, so a wheel that lacks a module or a data file fails
before release.

    python tools/smoke_mcp.py PATH/TO/sealed-panel
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from typing import Any

# Seconds the server may take to start and answer.
TIMEOUT_S = 60
PROTOCOL_VERSION = "2025-06-18"


def initialize(command: str) -> dict[str, Any]:
    """Start `command mcp` in an empty directory, send `initialize`, and return the result.

    Args:
        command: Path to the `sealed-panel` executable to check.

    Returns:
        The `result` object of the server's answer.

    Raises:
        RuntimeError: The server exited, answered an error, or answered something else.
    """
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "sealed-panel-smoke", "version": "0"},
        },
    }
    with tempfile.TemporaryDirectory() as root:
        proc = subprocess.Popen(
            [command, "mcp", "--root", root],
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            out, err = proc.communicate(json.dumps(request) + "\n", timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
    for line in out.splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("id") == 1:
            if "result" not in message:
                raise RuntimeError(f"initialize failed: {message.get('error')}")
            result: dict[str, Any] = message["result"]
            return result
    raise RuntimeError(f"no answer to initialize; stderr:\n{err[-2000:]}")


def main(argv: list[str] | None = None) -> int:
    """Check the server and print its name, version and protocol.

    Args:
        argv: The command-line arguments; the path to `sealed-panel` comes first.

    Returns:
        0 when the server answered `initialize`, 1 otherwise.
    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__)
        return 2
    try:
        result = initialize(args[0])
    except (OSError, RuntimeError) as exc:
        print(f"smoke_mcp: {exc}", file=sys.stderr)
        return 1
    info = result.get("serverInfo") or {}
    name, version, protocol = info.get("name"), info.get("version"), result.get("protocolVersion")
    print(f"{name} {version} answered initialize ({protocol})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
