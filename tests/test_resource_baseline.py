"""Darwin VM layout differences must not defeat resource-limit preflight."""
import os
import json
import resource
import subprocess
import sys
import unittest
from unittest.mock import patch

from agent.sandbox import runner


class ResourceBaselineTests(unittest.TestCase):
    def test_nested_launchers_never_raise_inherited_hard_limits(self):
        # The health target and its host client are profiled separately.
        # Process counts and Darwin VM maps can shrink between those probes.
        _, profile = runner.prepare_posix_resource_limited_command(
            ["true"], {}, cpu_seconds_per_process=5)
        larger = dict(profile)
        for key, delta in (("cpu_seconds_per_process", 1),
                           ("max_file_bytes", 1024), ("max_open_files", 1),
                           ("max_user_processes", 1),
                           ("max_address_space_bytes", 1024)):
            larger[key] += delta
        probe = [sys.executable, "-I", "-S", "-c",
                 "import json,resource; print(json.dumps({n:resource.getrlimit("
                 "getattr(resource,n)) for n in ('RLIMIT_CPU','RLIMIT_FSIZE',"
                 "'RLIMIT_NOFILE','RLIMIT_NPROC','RLIMIT_AS','RLIMIT_CORE')}))"]
        for outer, inner in ((profile, larger), (larger, profile), (profile, profile)):
            with self.subTest(outer_larger=outer is larger, inner_larger=inner is larger):
                result = subprocess.run(
                    runner._resource_limited_argv(
                        runner._resource_limited_argv(probe, inner), outer),
                    env={"PATH": os.defpath, "LC_ALL": "C"},
                    capture_output=True, text=True, timeout=5, check=False)
                self.assertEqual(0, result.returncode, result.stderr)
                limits = json.loads(result.stdout)
                for name, key in (("RLIMIT_CPU", "cpu_seconds_per_process"),
                                  ("RLIMIT_FSIZE", "max_file_bytes"),
                                  ("RLIMIT_NOFILE", "max_open_files"),
                                  ("RLIMIT_NPROC", "max_user_processes"),
                                  ("RLIMIT_AS", "max_address_space_bytes")):
                    self.assertEqual([min(outer[key], inner[key])] * 2, limits[name])
                self.assertEqual([0, 0], limits["RLIMIT_CORE"])

    def measure(self, values):
        results = [subprocess.CompletedProcess([], 0, str(value)) for value in values]
        with patch.object(runner.sys, "platform", "darwin"):
            with patch.object(runner.subprocess, "run", side_effect=results) as run:
                result = runner._address_space_baseline_bytes()
        return result, run

    def test_native_launcher_can_exceed_python_baseline(self):
        # Values captured by the failing arm64 Python 3.10 CI job.
        controller = 420832804864 // 1024
        launcher = 435299488
        baseline, run = self.measure([controller, launcher])
        self.assertEqual(launcher * 1024, baseline)
        self.assertEqual(2, run.call_count)
        self.assertEqual("/bin/bash", run.call_args_list[1].args[0][0])
        for call in run.call_args_list:
            self.assertEqual({"PATH": os.defpath}, call.kwargs["env"])
            self.assertTrue(call.kwargs["check"])
            self.assertEqual(3, call.kwargs["timeout"])
        with patch.object(resource, "getrlimit", return_value=(-1, resource.RLIM_INFINITY)):
            limit = runner._address_space_limit_bytes(baseline)
        self.assertEqual(runner.POC_MAX_ADDRESS_SPACE_BYTES, limit - baseline)

    def test_controller_can_exceed_launcher_baseline(self):
        self.assertEqual(200000 * 1024, self.measure([200000, 100000])[0])

    def test_invalid_measurement_cannot_fall_back_to_the_other_process(self):
        for bad in ("", "not-a-number", "0", "-1", str(2 ** 31), "100\n200"):
            for values in ([bad, "100"], ["100", bad]):
                with self.subTest(values=values):
                    with self.assertRaises(PermissionError):
                        self.measure(values)

    def test_launcher_timeout_fails_closed(self):
        with patch.object(runner.sys, "platform", "darwin"):
            with patch.object(runner.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0, "1000"),
                    subprocess.TimeoutExpired("/bin/bash", 3)]):
                with self.assertRaises(PermissionError):
                    runner._address_space_baseline_bytes()

    def test_non_darwin_keeps_absolute_limit_without_vm_probe(self):
        with patch.object(runner.sys, "platform", "linux"):
            with patch.object(runner.subprocess, "run") as run:
                self.assertEqual(0, runner._address_space_baseline_bytes())
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
