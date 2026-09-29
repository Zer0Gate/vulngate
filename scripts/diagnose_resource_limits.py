#!/usr/bin/env python3
"""Read-only CI diagnostics for the controller and native resource launcher.

This does not change limits or replace the runner's fail-closed preflight.
Only runtime identity, VM size and resource limits are printed, never env values.
"""
import json
import os
import platform
import resource
import subprocess
import sys


def main() -> None:
    from agent.sandbox.runner import (
        _address_space_baseline_bytes,
        _address_space_limit_bytes,
    )

    report = {
        "python": platform.python_version(),
        "machine": platform.machine(),
        "platform": sys.platform,
        "controller_rlimit_as": list(resource.getrlimit(resource.RLIMIT_AS)),
    }
    try:
        baseline = _address_space_baseline_bytes()
        report["controller_baseline_bytes"] = baseline
        report["requested_address_space_bytes"] = _address_space_limit_bytes(baseline)
        # The final ':' keeps bash alive so ps measures bash, not an exec'd ps.
        launcher = subprocess.run(
            ["/bin/bash", "-c",
             '/usr/bin/uname -m; /bin/ps -o vsz= -p "$$"; ulimit -S -v; ulimit -H -v; :'],
            env={"PATH": os.defpath}, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, check=True, timeout=5)
        report["native_launcher_machine_vsz_kib_soft_hard"] = launcher.stdout.splitlines()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report["diagnostic_error_type"] = type(exc).__name__
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
