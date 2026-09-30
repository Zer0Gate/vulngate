"""Trusted host launcher: wait for parent cgroup attachment before exec.

The target argv is sent only after the parent verifies attachment. EOF or an
invalid message exits without running the target. This file has no agent
imports so it can run with Python's isolated (-I) mode.
"""

from __future__ import annotations

import json
import os
import sys


MAX_COMMAND_BYTES = 65536


def main() -> int:
    if len(sys.argv) != 2:
        return 125
    try:
        fd = int(sys.argv[1])
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, min(4096, MAX_COMMAND_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_COMMAND_BYTES:
                return 125
        payload = json.loads(b"".join(chunks).decode("utf-8"))
        if not isinstance(payload, dict):
            return 125
        command = payload.get("command")
        env = payload.get("env")
        if (not isinstance(command, list) or not command
                or any(not isinstance(arg, str) or "\x00" in arg
                       for arg in command)
                or not isinstance(env, dict)
                or any(not isinstance(key, str) or not key or "=" in key
                       or "\x00" in key or not isinstance(value, str)
                       or "\x00" in value for key, value in env.items())):
            return 125
        os.execvpe(command[0], command, env)
    except (OSError, ValueError, UnicodeError, TypeError):
        return 125
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    return 125


if __name__ == "__main__":
    raise SystemExit(main())
