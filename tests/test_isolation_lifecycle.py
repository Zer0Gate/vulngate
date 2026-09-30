"""Backend identity, health execution and cleanup boundary regressions."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from agent.sandbox.isolation import ContainerBackend, LinuxBubblewrapBackend  # noqa: E402


class IsolationLifecycleTests(unittest.TestCase):
    def test_container_health_requires_owned_identity_and_maps_workspace(self):
        with tempfile.TemporaryDirectory() as td, patch(
                "agent.sandbox.isolation._version", return_value="test"):
            root = Path(td)
            backend = ContainerBackend("/usr/bin/docker", "fixture-image")
            with self.assertRaises(PermissionError):
                backend.health_command(["sh", str(root / "health.sh")], 123, {})
            start = backend.wrap_command(["sh", str(root / "service.sh")], root, root, {})
            self.assertIn("/workspace/service.sh", start)
            identity = "a" * 64
            with patch("agent.sandbox.isolation.subprocess.run", return_value=
                       subprocess.CompletedProcess([], 0, identity + "\n")):
                backend.after_start(123)
            health = backend.health_command(["sh", str(root / "health.sh")], 123,
                                            {"BASH_ENV": str(root / "hook.sh")})
            self.assertEqual(["/usr/bin/docker", "exec"], health[:2])
            self.assertIn(identity, health)
            self.assertEqual("/workspace/health.sh", health[-1])
            self.assertIn("BASH_ENV=/workspace/hook.sh", health)
            self.assertNotIn("BASH_ENV", backend.launcher_env())

    def test_container_close_removes_owned_id_and_requires_absence(self):
        with tempfile.TemporaryDirectory() as td, patch(
                "agent.sandbox.isolation._version", return_value="test"):
            backend = ContainerBackend("/usr/bin/docker", "fixture-image")
            backend.wrap_command(["sh", "service.sh"], Path(td), Path(td), {})
            identity = "b" * 64
            outcomes = [subprocess.CompletedProcess([], 0, identity + "\n"),
                        subprocess.CompletedProcess([], 0),
                        subprocess.CompletedProcess([], 0, "")]
            with patch("agent.sandbox.isolation.subprocess.run", side_effect=outcomes) as run:
                backend.close()
            self.assertEqual(["/usr/bin/docker", "rm", "--force", identity],
                             run.call_args_list[1].args[0])
            self.assertIsNone(backend._run_token)

    def test_container_survivor_or_engine_failure_is_not_clean(self):
        for engine_failed in (False, True):
            with self.subTest(engine_failed=engine_failed), tempfile.TemporaryDirectory() as td, patch(
                    "agent.sandbox.isolation._version", return_value="test"):
                backend = ContainerBackend("/usr/bin/podman", "fixture-image")
                backend.wrap_command(["sh", "service.sh"], Path(td), Path(td), {})
                outcome = subprocess.CompletedProcess([], 1 if engine_failed else 0,
                                                      "" if engine_failed else "c" * 64 + "\n")
                with patch("agent.sandbox.isolation.subprocess.run", return_value=outcome):
                    with self.assertRaises(OSError):
                        backend.close()
                self.assertIsNotNone(backend._run_token)

    def test_linux_identity_rejects_non_pid_and_closes_pipes(self):
        with tempfile.TemporaryDirectory() as td, patch(
                "agent.sandbox.isolation._version", return_value="test"):
            backend = LinuxBubblewrapBackend("/usr/bin/bwrap")
            backend.wrap_command(["sh", "service.sh"], Path(td), Path(td), {})
            reader, writer = backend._info_reader, backend._info_writer
            os.write(writer, b'{"child-pid":true}')
            with self.assertRaises(PermissionError):
                backend.after_start(123)
            backend.close()
            for fd in (reader, writer):
                with self.assertRaises(OSError):
                    os.fstat(fd)

    def test_linux_health_has_no_missing_nsenter_host_fallback(self):
        with patch("agent.sandbox.isolation._version", return_value="test"), patch(
                "agent.sandbox.isolation.shutil.which", return_value=None):
            backend = LinuxBubblewrapBackend("/usr/bin/bwrap")
            with self.assertRaises(PermissionError):
                backend.health_command(["sh", "health.sh"], 123, {})

    def test_linux_info_identity_alone_cannot_pin_host_setup_context(self):
        with tempfile.TemporaryDirectory() as td, patch(
                "agent.sandbox.isolation._version", return_value="test"):
            backend = LinuxBubblewrapBackend("/usr/bin/bwrap")
            backend.wrap_command(["sh", "service.sh"], Path(td), Path(td), {})
            os.write(backend._info_writer, b'{"child-pid":123}')
            # No sandbox helper ran; closing the parent writer yields EOF.
            with patch("agent.sandbox.isolation.os.open") as open_context:
                with self.assertRaisesRegex(PermissionError, "setup was not established"):
                    backend.after_start(123)
            open_context.assert_not_called()
            backend.close()

    def test_linux_health_uses_pinned_namespace_root_and_cwd_handles(self):
        with tempfile.TemporaryDirectory() as td, patch(
                "agent.sandbox.isolation._version", return_value="test"), patch(
                "agent.sandbox.isolation.shutil.which", return_value="/usr/bin/nsenter"):
            root = Path(td)
            backend = LinuxBubblewrapBackend("/usr/bin/bwrap")
            backend.workspace = backend.working_dir = root
            backend._leader_pid = 123
            backend._context_fds = dict(zip(
                ("user", "mnt", "net", "pid", "ipc", "uts", "root", "cwd"), range(30, 38)))
            health = backend.health_command(["sh", str(root / "health.sh")], 123,
                                            {"BASH_ENV": str(root / "hook.sh")})
            for option in ("user", "mount", "net", "pid", "ipc", "uts", "root", "wd"):
                self.assertTrue(any(arg.startswith("--" + option + "=/proc/self/fd/")
                                    for arg in health), option)
            self.assertNotIn("-t", health)
            self.assertEqual("/workspace/health.sh", health[-1])
            self.assertIn("BASH_ENV=/workspace/hook.sh", health)


if __name__ == "__main__":
    unittest.main()
