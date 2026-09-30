"""Opt-in real backend acceptance; dedicated CI must not skip missing tools.

Run only on an ephemeral Linux CI host as root. Ordinary cross-platform unit
jobs skip this suite; the stable required test check also requires its CI job.
"""

import os
import platform
import shutil
import sys
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from agent.orchestrator.config import TargetConfig
from agent.sandbox.approval import ApprovalGate
from agent.sandbox.isolation import CgroupV2Controller
from agent.tools.service_lifecycle import ServiceLifecycle


@unittest.skipUnless(os.environ.get("VULNGATE_LIVE_ISOLATION") == "1",
                     "real isolation runs in the dedicated Linux CI job")
class LiveIsolationBackendTests(unittest.TestCase):
    def setUp(self):
        # Only synthetic fixture stderr, bounded and printed for this live CI
        # job. Production lifecycle output remains suppressed.
        self.diagnostics = self.enterContext(tempfile.TemporaryFile())
        original_popen = subprocess.Popen

        def fixture_popen(*args, **kwargs):
            if kwargs.get("stderr") == subprocess.DEVNULL:
                kwargs["stderr"] = self.diagnostics
            return original_popen(*args, **kwargs)

        self.enterContext(patch("subprocess.Popen", side_effect=fixture_popen))

    def tearDown(self):
        self.diagnostics.seek(0, os.SEEK_END)
        self.diagnostics.seek(max(0, self.diagnostics.tell() - 8192))
        output = self.diagnostics.read().decode("utf-8", "replace")
        if output:
            print("Synthetic live fixture diagnostics:\n" + output, file=sys.stderr)

    @classmethod
    def setUpClass(cls):
        if (platform.system() != "Linux" or os.geteuid() != 0
                or os.environ.get("GITHUB_ACTIONS") != "true"):
            raise RuntimeError("live suite requires an ephemeral Linux CI root context")
        for name in ("bwrap", "nsenter", "docker"):
            if not shutil.which(name):
                raise RuntimeError("required real backend tool missing: " + name)
        cls.image = os.environ.get("VULNGATE_LIVE_IMAGE", "")
        if not cls.image.startswith("sha256:"):
            raise RuntimeError("live container fixture must use a resolved image ID")
        cls.parent = CgroupV2Controller.ROOT / ("vulngate-ci-" + uuid.uuid4().hex)
        # Only this disposable CI environment may enable root controllers.
        # Each service gets its own child below this dedicated parent.
        (CgroupV2Controller.ROOT / "cgroup.subtree_control").write_text(
            "+cpu +memory +pids", encoding="ascii")
        cls.parent.mkdir()
        cls.addClassCleanup(cls.parent.rmdir)
        (cls.parent / "cgroup.subtree_control").write_text(
            "+cpu +memory +pids", encoding="ascii")
        cls.root_patch = patch.object(CgroupV2Controller, "ROOT", cls.parent)
        cls.root_patch.start()
        cls.addClassCleanup(cls.root_patch.stop)

    def fixture(self, backend, *, timeout_health=False):
        workspace = Path(self.enterContext(tempfile.TemporaryDirectory(
            prefix="vulngate-live-workspace-")))
        host_only = Path(self.enterContext(tempfile.TemporaryDirectory(
            prefix="vulngate-host-only-"))) / "canary"
        host_only.write_text("host-only", encoding="utf-8")
        (workspace / "service.sh").write_text(
            "#!/bin/sh\nset -eu\n"
            "test \"$PWD\" = /workspace\n"
            "cat /proc/self/status > /workspace/service-status\n"
            "cat /proc/self/limits > /workspace/service-limits\n"
            "printf started > /workspace/started\n"
            "setsid sh -c 'sleep 60' &\n"
            "while :; do sleep 1; done\n", encoding="utf-8")
        (workspace / "health.sh").write_text(
            "#!/bin/sh\nset -eu\n"
            "test \"$PWD\" = /workspace\n"
            "test \"$VULNGATE_SERVICE_HEALTHCHECK\" = true\n"
            "test \"$FIXTURE_PATH\" = /workspace/started\n"
            "test ! -e '" + str(host_only) + "'\n"
            "test -f /workspace/started\n"
            "cat /proc/self/cgroup > /workspace/health-cgroup\n"
            "cat /proc/self/status > /workspace/health-status\n"
            "cat /proc/self/limits > /workspace/health-limits\n"
            "printf isolated > /workspace/healthy\n"
            + ("setsid sh -c 'sleep 60' &\nsleep 60\n" if timeout_health else ""),
            encoding="utf-8")
        config = TargetConfig(
            name="live-isolation", discovery_date="2026-10-01",
            runtime_lab={"service": {
                "isolation_backend": backend,
                "isolation_image": self.image if backend == "docker" else "",
                "start_command": ["sh", str(workspace / "service.sh")],
                "healthcheck_command": ["sh", str(workspace / "health.sh")],
                "env": {"FIXTURE_PATH": str(workspace / "started")},
                "startup_timeout": 10, "health_timeout": 2,
                "shutdown_timeout": 2, "poll_interval": 0.05,
            }})
        lifecycle = ServiceLifecycle(workspace, "live-isolation", 1, config,
                                     approval=ApprovalGate())
        self.assertIsNotNone(lifecycle.isolation_backend,
                             lifecycle.isolation_descriptor.as_dict())
        lifecycle.approval.record_authorized(
            "service_lifecycle", "ephemeral CI fixture", run_id=lifecycle.run_id,
            config_digest=lifecycle.snapshot()["config_digest"],
            expires_at=time.time() + 120)
        self.addCleanup(lifecycle.stop)
        return workspace, lifecycle

    def assert_ready_and_clean(self, backend):
        workspace, lifecycle = self.fixture(backend)
        with patch.object(lifecycle.runner, "run",
                          side_effect=AssertionError("host health runner invoked")):
            ready = lifecycle.ensure_ready()
        self.assertTrue(ready["ready"], ready)
        self.assertEqual("isolated", (workspace / "healthy").read_text())
        self.assertEqual("managed-service-backend", ready["health"]["execution_context"])
        for phase in ("service", "health"):
            status = dict(line.split(":", 1) for line in (workspace / (phase + "-status"))
                          .read_text().splitlines() if ":" in line)
            for name in ("CapEff", "CapBnd", "CapAmb", "CapInh"):
                self.assertEqual(0, int(status[name].strip(), 16), (phase, status))
            self.assertEqual("1", status["NoNewPrivs"].strip())
            limits = (workspace / (phase + "-limits")).read_text()
            file_limit = next(line for line in limits.splitlines() if line.startswith("Max file size"))
            self.assertEqual(["67108864", "67108864", "bytes"], file_limit.split()[-3:])
        controller = lifecycle.cgroup_controller
        if controller is not None:
            self.assertIn(controller.path.name, (workspace / "health-cgroup").read_text())
            self.assertTrue(controller.contains(lifecycle.process.pid))
            for name, value in controller.limits.items():
                self.assertEqual(value, " ".join((controller.path / name).read_text().split()))
        concrete = lifecycle.isolation_backend
        token = getattr(concrete, "_run_token", None)
        stopped = lifecycle.stop()
        self.assertTrue(stopped["stopped"], stopped)
        self.assertEqual("complete", stopped["backend_cleanup_status"])
        if controller is not None:
            self.assertFalse(controller.path.exists())
            self.assertEqual({}, concrete._context_fds)
        if token:
            result = subprocess.run(
                [concrete.executable, "ps", "-aq", "--filter", "label=vulngate.run=" + token],
                env=concrete.launcher_env(), capture_output=True, text=True,
                timeout=5, check=True)
            self.assertEqual("", result.stdout.strip())

    def test_bubblewrap_health_identity_limits_and_detached_cleanup(self):
        self.assert_ready_and_clean("bubblewrap")

    def test_container_health_identity_and_detached_cleanup(self):
        self.assert_ready_and_clean("docker")

    def test_real_backend_health_timeout_tears_down_unit(self):
        for backend in ("bubblewrap", "docker"):
            with self.subTest(backend=backend):
                workspace, lifecycle = self.fixture(backend, timeout_health=True)
                result = lifecycle.ensure_ready()
                self.assertFalse(result["ready"], result)
                self.assertTrue(result.get("health", {}).get("timed_out"), result)
                self.assertTrue((workspace / "healthy").exists())
                self.assertTrue(result["stop"]["stopped"], result)
                self.assertIsNone(lifecycle.process)
                self.assertIsNone(lifecycle.cgroup_controller)

    def test_real_cleanup_failure_retains_lock_until_retry(self):
        for backend in ("bubblewrap", "docker"):
            with self.subTest(backend=backend):
                _workspace, lifecycle = self.fixture(backend)
                ready = lifecycle.ensure_ready()
                self.assertTrue(ready["ready"], ready)
                owner = lifecycle.cgroup_controller or lifecycle.isolation_backend
                with patch.object(owner, "close", side_effect=OSError("injected cleanup failure")):
                    failed = lifecycle.stop()
                self.assertEqual("cleanup-incomplete", failed["status"])
                self.assertFalse(failed["stopped"])
                self.assertIsNotNone(lifecycle._lock_handle)
                retried = lifecycle.stop()
                self.assertTrue(retried["stopped"], retried)
                self.assertIsNone(lifecycle._lock_handle)


if __name__ == "__main__":
    unittest.main()
