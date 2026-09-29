"""Darwin VM layout differences must not defeat resource-limit preflight."""
import os
import resource
import subprocess
import unittest
from unittest.mock import patch

from agent.sandbox import runner


class ResourceBaselineTests(unittest.TestCase):
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
