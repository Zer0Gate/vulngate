import socket
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from agent.orchestrator.config import TargetConfig  # noqa: E402
from agent.tools.service_lifecycle import (  # noqa: E402
    ServiceLifecycle,
    _loopback_connect_host,
)
from agent.sandbox.approval import ApprovalGate  # noqa: E402
from agent.sandbox.isolation import CgroupV2Controller, IsolationDescriptor  # noqa: E402
from agent.sandbox.runner import prepare_posix_resource_limited_command  # noqa: E402


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _nc_http_start_command(root, port):
    """Create a portable loopback fixture for lifecycle subprocess tests.

    Current macOS-latest GitHub runners can leave Python's listener in CLOSED
    rather than LISTEN, while the runner's netcat listener works normally.
    The fixture still exercises the real lifecycle boundary: it is a child
    process, binds only to loopback, returns an HTTP status, and stays alive
    until ServiceLifecycle terminates its process group.
    """
    fixture = (root / "loopback-http-fixture.sh").resolve()
    fixture.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "port=\"$1\"\n"
        "while :; do\n"
        "  printf 'HTTP/1.0 200 OK\\r\\nContent-Length: 0\\r\\n'\n"
        "  printf 'Connection: close\\r\\n\\r\\n'\n"
        "  sleep 1\n"
        "done | nc -lk 127.0.0.1 \"$port\"\n",
        encoding="utf-8",
    )
    fixture.chmod(fixture.stat().st_mode | 0o700)
    return ["sh", str(fixture), str(port)]


class _TestIsolationBackend:
    """Explicitly injected unit-test backend; production never uses this."""

    descriptor = IsolationDescriptor(
        backend="test-injected", version="unit", available=True,
        network="test-only", filesystem="test-only",
        capabilities=("injected",),
    )

    def wrap_command(self, command, workspace, working_dir, env):
        return list(command)

    def health_command(self, command, pid):
        return list(command)


class _GateTestCgroup:
    def __init__(self, marker, *, fail_attach=False, fail_close=False):
        self.marker = marker
        self.fail_attach = fail_attach
        self.fail_close = fail_close
        self.marker_seen_before_attach = None
        self.attached_pid = None
        self.close_called = False

    def attach(self, pid):
        time.sleep(0.2)
        self.marker_seen_before_attach = self.marker.exists()
        if self.fail_attach:
            raise OSError("injected cgroup attach failure")
        self.attached_pid = pid

    def snapshot(self):
        return {"backend": "cgroup-v2", "attached_pid": self.attached_pid,
                "enforced": self.attached_pid is not None}

    def close(self):
        self.close_called = True
        if self.fail_close:
            raise OSError("injected cgroup cleanup failure")


class _GateTestBackend(_TestIsolationBackend):
    descriptor = IsolationDescriptor(
        backend="linux-bubblewrap", version="unit", available=True,
        network="test-only", filesystem="test-only", capabilities=("cgroup-v2",),
    )

    def __init__(self, cgroup):
        self.cgroup = cgroup

    def prepare_cgroup(self, workspace, name):
        return self.cgroup

    def wrap_command(self, command, workspace, working_dir, env):
        # Simulate bwrap's --setenv inside the gate, after cgroup attachment.
        return ["/usr/bin/env", *("%s=%s" % item for item in sorted(env.items())),
                *command]


class _FakeCgroupFiles:
    def __init__(self):
        self.members = ""
        self.populated = "1"
        self.killed = False
        self.removed = False

    def __truediv__(self, name):
        parent = self

        class File:
            def write_text(self, value, encoding):
                if name == "cgroup.procs":
                    parent.members = value
                elif name == "cgroup.kill":
                    parent.killed = value == "1"
                    parent.populated = "0"

            def read_text(self, encoding):
                if name == "cgroup.procs":
                    return parent.members
                if name == "cgroup.events":
                    return "populated " + parent.populated + "\n"
                return ""

        return File()

    def rmdir(self):
        self.removed = True


def _authorize(lifecycle):
    lifecycle.approval.record_authorized(
        "service_lifecycle", "unit-test target service", run_id=lifecycle.run_id,
        config_digest=lifecycle.snapshot()["config_digest"],
        expires_at=time.time() + 60)


class ServiceLifecycleTests(unittest.TestCase):
    def test_cgroup_gate_eof_exits_without_target_execution(self):
        gate = ROOT / "scripts/agent/sandbox/cgroup_gate.py"
        reader, writer = os.pipe()
        try:
            process = subprocess.Popen(
                [sys.executable, "-I", "-S", "-B", str(gate), str(reader)],
                pass_fds=(reader,), stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            os.close(reader)
            reader = -1
            self.assertIsNone(process.poll())
            os.close(writer)
            writer = -1
            self.assertEqual(125, process.wait(timeout=2))
        finally:
            for fd in (reader, writer):
                if fd >= 0:
                    os.close(fd)

    def test_cgroup_controller_confirms_membership_and_kills_group(self):
        files = _FakeCgroupFiles()
        controller = CgroupV2Controller(files)
        controller.attach(1234)
        self.assertEqual(1234, controller.snapshot()["attached_pid"])
        controller.close()
        self.assertTrue(files.killed)
        self.assertTrue(files.removed)

    def _gated_fixture(self, root, cgroup, env=None):
        marker = cgroup.marker.resolve()
        start = (root / "start-service.sh").resolve()
        start.write_text("#!/bin/sh\nset -eu\nprintf started > '" +
                         str(marker) + "'\nsleep 30\n", encoding="utf-8")
        health = (root / "health-service.sh").resolve()
        health.write_text("#!/bin/sh\ntest -f '" + str(marker) + "'\n",
                          encoding="utf-8")
        cfg = TargetConfig(
            name="gated-service", discovery_date="2026-09-21",
            runtime_lab={"service": {
                "start_command": ["sh", str(start)],
                "healthcheck_command": ["sh", str(health)],
                "env": env or {},
                "startup_timeout": 3, "poll_interval": 0.05,
            }},
        )
        backend = _GateTestBackend(cgroup)
        with patch("agent.tools.service_lifecycle.detect_isolation_backend",
                   return_value=(backend, backend.descriptor)):
            lifecycle = ServiceLifecycle(root, "gated-service", 1, cfg,
                                         approval=ApprovalGate())
        _authorize(lifecycle)
        return lifecycle

    def test_cgroup_gate_holds_target_until_attach_and_releases_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            marker = root / "target-started"
            cgroup = _GateTestCgroup(marker)
            lifecycle = self._gated_fixture(root, cgroup)
            ready = lifecycle.ensure_ready()
            try:
                self.assertTrue(ready["ready"], ready)
                self.assertFalse(cgroup.marker_seen_before_attach)
                self.assertTrue(marker.exists())
                self.assertEqual(lifecycle.process.pid, cgroup.attached_pid)
            finally:
                stopped = lifecycle.stop()
            self.assertTrue(cgroup.close_called)
            self.assertEqual("complete", stopped["cgroup_cleanup_status"])

    def test_preflight_and_gate_do_not_execute_target_env_before_attach(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            hook_marker = root / "bash-env-ran"
            hook = root / "bash-env.sh"
            hook.write_text("printf '%s' \"$VULNGATE_TEST_ENV\" > '" +
                            str(hook_marker) + "'\n", encoding="utf-8")
            env = {"BASH_ENV": str(hook), "VULNGATE_TEST_ENV": "target-only"}
            prepare_posix_resource_limited_command(["sh", "-c", "true"], env)
            self.assertFalse(hook_marker.exists(), "preflight sourced target BASH_ENV")
            cgroup = _GateTestCgroup(root / "target-started")
            lifecycle = self._gated_fixture(root, cgroup, env)
            original_attach = cgroup.attach

            def check_before_attach(pid):
                self.assertFalse(hook_marker.exists(), "gate ran target environment")
                original_attach(pid)

            with patch.object(cgroup, "attach", side_effect=check_before_attach):
                ready = lifecycle.ensure_ready()
            try:
                self.assertTrue(ready["ready"], ready)
                self.assertEqual("target-only", hook_marker.read_text(encoding="utf-8"))
            finally:
                lifecycle.stop()

    def test_cgroup_attach_failure_never_releases_target(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            marker = root / "target-started"
            cgroup = _GateTestCgroup(marker, fail_attach=True)
            lifecycle = self._gated_fixture(root, cgroup)
            result = lifecycle.ensure_ready()
            self.assertFalse(result["ready"], result)
            self.assertEqual("run-failed", result["status"])
            self.assertFalse(cgroup.marker_seen_before_attach)
            self.assertFalse(marker.exists())
            self.assertTrue(cgroup.close_called)

    def test_cgroup_cleanup_failure_is_not_reported_as_stopped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cgroup = _GateTestCgroup(root / "target-started", fail_close=True)
            lifecycle = self._gated_fixture(root, cgroup)
            self.assertTrue(lifecycle.ensure_ready()["ready"])
            stopped = lifecycle.stop()
            self.assertEqual("cleanup-incomplete", stopped["status"])
            self.assertFalse(stopped["stopped"])
            self.assertEqual("incomplete", stopped["cgroup_cleanup_status"])
            cgroup.fail_close = False
            lifecycle.stop()

    def test_verified_cgroup_cleanup_overrides_pid_sampling_gap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cgroup = _GateTestCgroup(root / "target-started")
            lifecycle = self._gated_fixture(root, cgroup)
            self.assertTrue(lifecycle.ensure_ready()["ready"])
            lifecycle.process.kill()
            lifecycle.process.wait(timeout=2)
            limits = lifecycle.resource_limits["process_tree_rss"]
            limits["cleanup_status"] = "unverified"
            with patch.object(lifecycle, "_cleanup_managed_process_tree",
                              return_value="cleanup-incomplete"):
                stopped = lifecycle.stop()
            self.assertTrue(stopped["stopped"], stopped)
            self.assertEqual("stopped", stopped["status"])
            self.assertEqual("complete", stopped["process_tree_cleanup_status"])

    def test_healthcheck_hosts_never_require_external_name_resolution(self):
        self.assertEqual(_loopback_connect_host("localhost"), "127.0.0.1")
        self.assertEqual(_loopback_connect_host("localhost.localdomain"),
                         "127.0.0.1")
        self.assertEqual(_loopback_connect_host("127.9.8.7"), "127.9.8.7")
        self.assertEqual(_loopback_connect_host("::1"), "::1")
        self.assertEqual(_loopback_connect_host("health.example.invalid"), "")
        self.assertEqual(_loopback_connect_host("192.0.2.10"), "")

    def test_starts_healthchecks_and_kills_owned_process_group(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            port = _free_port()
            cfg = TargetConfig(
                name="service-lab", discovery_date="2026-09-21",
                runtime_lab={"service": {
                    "start_command": _nc_http_start_command(root, port),
                    "allow_unconfined_start": True,
                    "healthcheck_url": "http://127.0.0.1:%d/" % port,
                    "startup_timeout": 5,
                    "poll_interval": 0.05,
                }},
            )
            approval = ApprovalGate()
            with patch("agent.tools.service_lifecycle.detect_isolation_backend",
                       return_value=(_TestIsolationBackend(),
                                     _TestIsolationBackend.descriptor)):
                lifecycle = ServiceLifecycle(root, "service-lab", 1, cfg,
                                             approval=approval)
            _authorize(lifecycle)
            ready = lifecycle.ensure_ready()
            self.assertTrue(ready["ready"], ready)
            self.assertEqual(ready["status"], "started-ready")
            self.assertTrue(ready.get("process_managed"))
            registry = root / "state" / "service-lab" / "round-01" / "S4" / "processes.json"
            self.assertTrue(registry.exists())
            self.assertTrue(json.loads(registry.read_text(encoding="utf-8"))["processes"][-1]["active"])
            stopped = lifecycle.stop()
            self.assertTrue(stopped["stopped"], stopped)
            self.assertIn(stopped["status"], {"stopped", "killed-after-timeout"})
            self.assertFalse(json.loads(registry.read_text(encoding="utf-8"))["processes"][-1]["active"])

    def test_loopback_healthcheck_ignores_host_proxy_environment(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            port = _free_port()
            cfg = TargetConfig(
                name="proxied-service", discovery_date="2026-09-21",
                runtime_lab={"service": {
                    "start_command": _nc_http_start_command(root, port),
                    "allow_unconfined_start": True,
                    "healthcheck_url": "http://127.0.0.1:%d/" % port,
                    "startup_timeout": 5,
                    "poll_interval": 0.05,
                }},
            )
            approval = ApprovalGate()
            with patch("agent.tools.service_lifecycle.detect_isolation_backend",
                       return_value=(_TestIsolationBackend(),
                                     _TestIsolationBackend.descriptor)):
                lifecycle = ServiceLifecycle(root, "proxied-service", 1, cfg,
                                             approval=approval)
            _authorize(lifecycle)
            proxy_env = {
                "HTTP_PROXY": "http://127.0.0.1:1",
                "HTTPS_PROXY": "http://127.0.0.1:1",
                "ALL_PROXY": "http://127.0.0.1:1",
            }
            with patch.dict("os.environ", proxy_env, clear=False), \
                    patch.dict("os.environ", {"NO_PROXY": "", "no_proxy": ""},
                               clear=False):
                ready = lifecycle.ensure_ready()
            try:
                self.assertTrue(ready["ready"], ready)
                self.assertEqual(ready["status"], "started-ready")
            finally:
                lifecycle.stop()

    def test_non_loopback_healthcheck_is_a_policy_gap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = TargetConfig(
                name="remote-health", discovery_date="2026-09-21",
                runtime_lab={"service": {
                    "healthcheck_url": "https://example.invalid/health",
                }},
            )
            result = ServiceLifecycle(root, "remote-health", 1, cfg).ensure_ready()
            self.assertFalse(result["ready"])
            self.assertEqual(result["status"], "precondition-unavailable")
            self.assertEqual(result["health"]["status"], "policy-denied")

    def test_missing_healthcheck_never_launches_a_process(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = TargetConfig(
                name="no-health", discovery_date="2026-09-21",
                runtime_lab={"service": {
                    "start_command": [sys.executable, "-m", "http.server", "0"],
                }},
            )
            lifecycle = ServiceLifecycle(root, "no-health", 1, cfg)
            result = lifecycle.ensure_ready()
            self.assertFalse(result["ready"])
            self.assertEqual(result["status"], "precondition-unavailable")
            self.assertFalse((root / "state" / "no-health" / "round-01" /
                              "S4" / "service-lifecycle.log").exists())

    def test_managed_start_requires_explicit_unconfined_opt_in(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            port = _free_port()
            cfg = TargetConfig(
                name="unconfined-opt-in", discovery_date="2026-09-21",
                runtime_lab={"service": {
                    "start_command": _nc_http_start_command(root, port),
                    "healthcheck_url": "http://127.0.0.1:%d/" % port,
                    "startup_timeout": 1,
                }},
            )
            lifecycle = ServiceLifecycle(root, "unconfined-opt-in", 1, cfg)
            result = lifecycle.ensure_ready()
            self.assertFalse(result["ready"])
            self.assertEqual(result["status"], "policy-denied")
            self.assertIn("available isolation backend", result["reason"])
            self.assertFalse(result["allow_unconfined_start"])
            self.assertIsNone(lifecycle.process)
            self.assertEqual(lifecycle.approval.decisions[-1]["operation"],
                             "policy_denied")
            self.assertEqual(lifecycle.approval.decisions[-1]["allowed"], False)

    def test_external_ready_service_is_not_stopped_implicitly(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = TargetConfig(
                name="external", discovery_date="2026-09-21",
                runtime_lab={"service": {
                    "healthcheck_url": "http://127.0.0.1:1/health",
                    "stop_command": [sys.executable, "-m", "http.server", "0"],
                }},
            )
            lifecycle = ServiceLifecycle(root, "external", 1, cfg)
            stopped = lifecycle.stop()
            self.assertEqual(stopped["status"], "not-managed")
            self.assertFalse(stopped["stopped"])


if __name__ == "__main__":
    unittest.main()
