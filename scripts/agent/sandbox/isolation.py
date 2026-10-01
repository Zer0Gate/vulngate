"""Runtime-lab isolation backends.

The service lifecycle used to launch a target directly on the host after a
boolean opt-in.  This module makes the isolation decision explicit and
fail-closed.  Backends are deliberately small: they report the exact backend
identity, wrap an argv command, and expose the capability contract that is
persisted with the service artifact.

The Linux backend uses bubblewrap when available.  It creates a private mount,
PID, IPC, UTS and network namespace, exposes only the workspace plus the
standard runtime directories, and gives the service a loopback-only network
namespace.  A configured container backend is also supported for macOS and
Linux.  Seatbelt is intentionally not used as a runtime-lab substitute: it is
the PoC backend, not a reliable target-service boundary.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import hashlib
import os
import stat
import time
import uuid
import json
import re
import select
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, TypeAlias

from ..orchestrator.security_types import IsolationState


ISOLATION_SCHEMA_VERSION = "isolation-backend-v1"
_CONTROLLER_READ_ONLY_DIRS = ("ledger", "reports", "poc")


def _prepare_controller_output_mounts(workspace: Path) -> Dict[str, Path]:
    """Validate reserved output paths before exposing a service workspace."""
    root = workspace.resolve(strict=True)
    paths: Dict[str, Path] = {}
    for name in ("state", *_CONTROLLER_READ_ONLY_DIRS):
        path = root / name
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        try:
            info = path.lstat()
        except OSError as exc:
            raise PermissionError("controller output mount is unavailable: " + name) from exc
        if not stat.S_ISDIR(info.st_mode):
            raise PermissionError("controller output mount is not a real directory: " + name)
        paths[name] = path
    return paths


def _version(executable: str) -> str:
    try:
        result = subprocess.run(
            [executable, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return " ".join((result.stdout or "").split())[:160] or "unknown"


def _contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


IsolationDescriptor: TypeAlias = IsolationState


def _sandbox_path(value: str, workspace: Path) -> str:
    """Translate a whole workspace path, never substrings in payload data."""
    path = Path(value)
    if path.is_absolute() and _contained(path, workspace):
        relative = path.resolve().relative_to(workspace.resolve())
        return "/workspace" if relative == Path(".") else "/workspace/" + str(relative)
    return value


def _sandbox_command(command: Sequence[str], workspace: Path) -> List[str]:
    return [_sandbox_path(str(value), workspace) for value in command]


class IsolationBackend:
    """Minimal backend contract used by :class:`ServiceLifecycle`."""

    descriptor: IsolationDescriptor

    def wrap_command(self, command: Sequence[str], workspace: Path,
                     working_dir: Path, env: Dict[str, str]) -> List[str]:
        raise NotImplementedError

    def launcher_env(self) -> Dict[str, str]:
        return {"PATH": os.defpath, "LC_ALL": "C"}

    def approval_identity(self) -> Dict[str, str]:
        return {}

    def start_fds(self) -> Tuple[int, ...]:
        return ()

    def after_start(self, pid: int) -> Optional[int]:
        return None

    def health_fds(self) -> Tuple[int, ...]:
        return ()

    def health_command(self, command: Sequence[str], pid: Optional[int],
                       env: Dict[str, str]) -> List[str]:
        """Produce an isolated health invocation; never fall back to the host."""
        raise PermissionError("backend has no isolated healthcheck implementation")

    def close(self) -> None:
        """Release this backend's resources, raising if cleanup is unverified."""

    def prepare_cgroup(self, workspace: Path, name: str) -> Optional["CgroupV2Controller"]:
        return None


class CgroupV2Controller:
    """Per-launch delegated cgroup v2 with verified attach and cleanup."""

    ROOT = Path("/sys/fs/cgroup")

    def __init__(self, path: Path):
        self.path = path
        self.limits = {
            "memory.max": str(2 * 1024 * 1024 * 1024),
            "pids.max": "128",
            "cpu.max": "400000 100000",
        }
        self.attached_pid: Optional[int] = None

    @classmethod
    def available(cls) -> bool:
        return (platform.system().lower() == "linux"
                and (cls.ROOT / "cgroup.controllers").is_file()
                and os.access(cls.ROOT, os.W_OK))

    @classmethod
    def create(cls, workspace: Path, name: str) -> Optional["CgroupV2Controller"]:
        if not cls.available():
            return None
        suffix = hashlib.sha256((str(workspace) + name).encode()).hexdigest()[:16]
        path = cls.ROOT / ("vulngate-" + suffix + "-" + uuid.uuid4().hex[:12])
        try:
            path.mkdir()
            controller = cls(path)
            if any(not (path / filename).exists() for filename in (
                    "cgroup.procs", "cgroup.events", "cgroup.kill")):
                raise OSError("delegated cgroup lacks verified cleanup support")
            for filename, value in controller.limits.items():
                (path / filename).write_text(value, encoding="ascii")
                if " ".join((path / filename).read_text(encoding="ascii").split()) != value:
                    raise OSError("delegated cgroup limit did not take effect")
            return controller
        except OSError:
            try:
                path.rmdir()
            except OSError:
                pass
            return None

    def attach(self, pid: int) -> None:
        (self.path / "cgroup.procs").write_text(str(int(pid)), encoding="ascii")
        if str(int(pid)) not in (self.path / "cgroup.procs").read_text(
                encoding="ascii").split():
            raise OSError("cgroup membership readback failed")
        if self.attached_pid is None:
            self.attached_pid = int(pid)

    def contains(self, pid: int) -> bool:
        return str(int(pid)) in (self.path / "cgroup.procs").read_text(
            encoding="ascii").split()

    def snapshot(self) -> Dict[str, Any]:
        return {"backend": "cgroup-v2", "path_digest": hashlib.sha256(
            str(self.path).encode()).hexdigest()[:20], "limits": dict(self.limits),
                "attached_pid": self.attached_pid, "enforced": self.attached_pid is not None}

    def close(self) -> None:
        def populated() -> bool:
            events = dict(line.split(maxsplit=1) for line in (
                self.path / "cgroup.events").read_text(encoding="ascii").splitlines()
                if len(line.split(maxsplit=1)) == 2)
            if events.get("populated") not in {"0", "1"}:
                raise OSError("cgroup population status unavailable")
            return events["populated"] == "1"

        if populated():
            (self.path / "cgroup.kill").write_text("1", encoding="ascii")
        deadline = time.monotonic() + 2.0
        while populated():
            if time.monotonic() >= deadline:
                raise OSError("cgroup remained populated after kill")
            time.sleep(0.05)
        self.path.rmdir()


class LinuxBubblewrapBackend(IsolationBackend):
    def __init__(self, executable: str):
        self.executable = executable
        self.workspace: Optional[Path] = None
        self.working_dir: Optional[Path] = None
        self._info_reader: Optional[int] = None
        self._info_writer: Optional[int] = None
        self._ready_reader: Optional[int] = None
        self._ready_writer: Optional[int] = None
        self._context_fds: Dict[str, int] = {}
        self._leader_pid: Optional[int] = None
        self.descriptor = IsolationDescriptor(
            backend="linux-bubblewrap",
            version=_version(executable),
            available=True,
            network="private-network-namespace-loopback-only",
            filesystem="workspace-rw-with-protected-controller-outputs",
            capabilities=(
                "mount-namespace", "pid-namespace", "network-namespace",
                "ipc-namespace", "uts-namespace", "read-only-system-runtime",
                "workspace-bind", "ephemeral-state-tmpfs",
                "read-only-controller-outputs", "die-with-parent", "cgroup-v2",
            ),
        )

    def wrap_command(self, command: Sequence[str], workspace: Path,
                     working_dir: Path, env: Dict[str, str]) -> List[str]:
        if self._info_reader is not None or self._context_fds:
            raise PermissionError("backend already owns a service context")
        self.workspace, self.working_dir = workspace.resolve(), working_dir.resolve()
        output_mounts = _prepare_controller_output_mounts(self.workspace)
        self._info_reader, self._info_writer = os.pipe()
        self._ready_reader, self._ready_writer = os.pipe()
        # Do not bind the host root. Only standard runtime paths and the audit
        # workspace are exposed; controller outputs are overmounted below.
        args = [
            self.executable, "--die-with-parent", "--new-session",
            "--unshare-user", "--unshare-pid", "--unshare-ipc",
            "--unshare-uts", "--unshare-net", "--proc", "/proc",
            "--cap-drop", "ALL",
            "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/workspace",
            "--bind", str(self.workspace), "/workspace",
            "--tmpfs", "/workspace/state",
            *[arg for name in _CONTROLLER_READ_ONLY_DIRS
              for arg in ("--ro-bind", str(output_mounts[name]),
                          "/workspace/" + name)],
            "--info-fd", str(self._info_writer), "--clearenv",
        ]
        for path in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc"):
            if Path(path).exists():
                args.extend(["--ro-bind", path, path])
        relative = "."
        if _contained(working_dir, workspace):
            relative = _sandbox_path(str(working_dir), workspace)
        args.extend(["--chdir", relative])
        for key, value in sorted(env.items()):
            # Environment keys/values have already passed the lifecycle
            # allowlist.  Bubblewrap receives them explicitly, never inherit
            # the host environment wholesale.
            args.extend(["--setenv", str(key), _sandbox_path(str(value), workspace)])
        args.append("--")
        # info-fd is emitted before child mount/chroot setup. A fixed isolated
        # interpreter signals only after bwrap has entered its final sandbox.
        # Do not use a target-writable ready file or arbitrary service marker.
        args.extend(["/usr/bin/python3", "-I", "-S", "-c",
                     "import os,sys; fd=int(sys.argv[1]); os.write(fd,b'1'); "
                     "os.close(fd); os.execvpe(sys.argv[2],sys.argv[2:],os.environ)",
                     str(self._ready_writer)])
        args.extend(_sandbox_command(command, workspace))
        return args

    def start_fds(self) -> Tuple[int, ...]:
        return tuple(fd for fd in (self._info_writer, self._ready_writer) if fd is not None)

    def after_start(self, pid: int) -> Optional[int]:
        if self._info_reader is None or self._info_writer is None:
            raise PermissionError("bubblewrap identity pipe is missing")
        os.close(self._info_writer)
        self._info_writer = None
        chunks = bytearray()
        deadline = time.monotonic() + 5
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self._info_reader], [], [], remaining)[0]:
                raise PermissionError("bubblewrap identity timed out")
            chunk = os.read(self._info_reader, 4096)
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > 8192:
                raise PermissionError("bubblewrap identity exceeded limit")
        os.close(self._info_reader)
        self._info_reader = None
        try:
            info = json.loads(chunks)
            child_pid = info["child-pid"]
            if type(child_pid) is not int or child_pid <= 0:
                raise ValueError("invalid child PID")
            if self._ready_reader is None or self._ready_writer is None:
                raise PermissionError("bubblewrap setup pipe is missing")
            os.close(self._ready_writer)
            self._ready_writer = None
            if (not select.select([self._ready_reader], [], [], max(
                    0, deadline - time.monotonic()))[0]
                    or os.read(self._ready_reader, 2) != b"1"):
                raise PermissionError("bubblewrap sandbox setup was not established")
            os.close(self._ready_reader)
            self._ready_reader = None
            # Pin the actual sandbox namespaces, root and cwd. A later PID
            # reuse cannot redirect nsenter into an unrelated host process.
            for name in ("user", "mnt", "net", "pid", "ipc", "uts"):
                fd = os.open("/proc/%d/ns/%s" % (child_pid, name),
                             os.O_RDONLY | os.O_CLOEXEC)
                self._context_fds[name] = fd
                if os.fstat(fd).st_ino == os.stat("/proc/self/ns/" + name).st_ino:
                    raise PermissionError("bubblewrap did not isolate " + name)
            flags = getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | os.O_CLOEXEC
            root = os.open("/proc/%d/root" % child_pid, flags)
            self._context_fds["root"] = root
            if (os.fstat(root).st_dev, os.fstat(root).st_ino) == (
                    os.stat("/").st_dev, os.stat("/").st_ino):
                raise PermissionError("bubblewrap root is the host root")
            if self.workspace is None or self.working_dir is None:
                raise PermissionError("bubblewrap workspace identity is missing")
            cwd = _sandbox_path(str(self.working_dir), self.workspace)
            self._context_fds["cwd"] = os.open("/proc/%d/root%s" % (child_pid, cwd), flags)
            self._leader_pid = pid
            return child_pid
        except (ValueError, KeyError, TypeError) as exc:
            raise PermissionError("invalid bubblewrap identity") from exc

    def health_fds(self) -> Tuple[int, ...]:
        return tuple(self._context_fds.values())

    def health_command(self, command: Sequence[str], pid: Optional[int],
                       env: Dict[str, str]) -> List[str]:
        nsenter = shutil.which("nsenter", path=os.defpath)
        if (not nsenter or pid != self._leader_pid or self.workspace is None
                or set(self._context_fds) != {"user", "mnt", "net", "pid", "ipc", "uts", "root", "cwd"}):
            raise PermissionError("verified bubblewrap health context is unavailable")
        options = {"user": "user", "mnt": "mount", "net": "net", "pid": "pid",
                   "ipc": "ipc", "uts": "uts", "root": "root", "cwd": "wd"}
        args = [nsenter, "--preserve-credentials"]
        args.extend("--%s=/proc/self/fd/%d" % (options[name], fd)
                    for name, fd in self._context_fds.items())
        args.extend(["--", "/usr/bin/setpriv", "--bounding-set=-all",
                     "--inh-caps=-all", "--ambient-caps=-all", "--no-new-privs",
                     "--", "/usr/bin/env", "-i"])
        args.extend("%s=%s" % (key, _sandbox_path(value, self.workspace))
                    for key, value in sorted(env.items()))
        return args + _sandbox_command(command, self.workspace)

    def close(self) -> None:
        fds = [*self._context_fds.values(), self._info_reader, self._info_writer,
               self._ready_reader, self._ready_writer]
        self._context_fds.clear()
        self._info_reader = self._info_writer = self._leader_pid = None
        self._ready_reader = self._ready_writer = None
        for fd in fds:
            if fd is not None:
                os.close(fd)

    def prepare_cgroup(self, workspace: Path, name: str) -> Optional[CgroupV2Controller]:
        return CgroupV2Controller.create(workspace, name)


class ContainerBackend(IsolationBackend):
    def __init__(self, executable: str, image: str):
        self.executable = executable
        self.image = image
        self.workspace: Optional[Path] = None
        self.working_dir: Optional[Path] = None
        self._run_token: Optional[str] = None
        self._container_id: Optional[str] = None
        self._launch_pending = False
        self.descriptor = IsolationDescriptor(
            backend="%s-runtime-container" % Path(executable).name,
            version=_version(executable),
            available=True,
            network="container-network-none",
            filesystem="workspace-rw-with-protected-controller-outputs",
            capabilities=("network-none", "read-only-root", "workspace-bind",
                           "ephemeral-state-tmpfs", "read-only-controller-outputs",
                           "cap-drop-all", "no-new-privileges", "pids-limit",
                           "memory-max-2g", "cpu-max-4"),
        )

    def wrap_command(self, command: Sequence[str], workspace: Path,
                     working_dir: Path, env: Dict[str, str]) -> List[str]:
        if self._run_token is not None:
            raise PermissionError("container backend already owns a service")
        self.workspace, self.working_dir = workspace.resolve(), working_dir.resolve()
        output_mounts = _prepare_controller_output_mounts(self.workspace)
        self._run_token = uuid.uuid4().hex
        relative = "."
        if _contained(working_dir, workspace):
            relative = _sandbox_path(str(working_dir), workspace)
        args = [
            self.executable, "run", "--rm", "--init", "--network", "none",
            "--name", "vulngate-" + self._run_token,
            "--label", "vulngate.run=" + self._run_token,
            "--read-only", "--memory", "2g", "--cpus", "4",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "128", "--tmpfs", "/tmp:rw,noexec,nosuid,nodev", "-v",
            "%s:/workspace:rw" % self.workspace,
            "--tmpfs", "/workspace/state:rw,noexec,nosuid,nodev,size=536870912",
            *[arg for name in _CONTROLLER_READ_ONLY_DIRS
              for arg in ("-v", "%s:/workspace/%s:ro" %
                          (output_mounts[name], name))],
            "-w", relative,
            "--ulimit", "core=0:0", "--ulimit", "fsize=67108864:67108864",
            "--ulimit", "nofile=512:512",
        ]
        for key, value in sorted(env.items()):
            args.extend(["-e", "%s=%s" % (key, _sandbox_path(value, workspace))])
        args.append(self.image)
        args.extend(_sandbox_command(command, workspace))
        return args

    def launcher_env(self) -> Dict[str, str]:
        env = super().launcher_env()
        for key in ("HOME", "XDG_RUNTIME_DIR"):
            if os.environ.get(key):
                env[key] = os.environ[key]
        return env

    def approval_identity(self) -> Dict[str, str]:
        return {"image_reference": self.image}

    def _owned_containers(self) -> List[str]:
        if self._run_token is None:
            return []
        result = subprocess.run(
            [self.executable, "ps", "--all", "--no-trunc", "--filter",
             "label=vulngate.run=" + self._run_token, "--format", "{{.ID}}"],
            env=self.launcher_env(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=5, check=False)
        ids = result.stdout.split()
        if result.returncode != 0 or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in ids):
            raise OSError("container ownership could not be verified")
        return ids

    def after_start(self, pid: int) -> Optional[int]:
        self._launch_pending = True
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            ids = self._owned_containers()
            if len(ids) == 1:
                self._container_id = ids[0]
                return None
            if len(ids) > 1:
                raise PermissionError("container ownership is ambiguous")
            time.sleep(0.05)
        raise PermissionError("container identity was not established")

    def health_command(self, command: Sequence[str], pid: Optional[int],
                       env: Dict[str, str]) -> List[str]:
        if not self._container_id or self.workspace is None or self.working_dir is None:
            raise PermissionError("verified container health context is unavailable")
        args = [self.executable, "exec", "--workdir",
                _sandbox_path(str(self.working_dir), self.workspace)]
        for key, value in sorted(env.items()):
            args.extend(["--env", "%s=%s" % (key, _sandbox_path(value, self.workspace))])
        return args + [self._container_id, *_sandbox_command(command, self.workspace)]

    def close(self) -> None:
        if self._run_token is None:
            return
        # Use engine-owned labels and immutable IDs, never a target-writable
        # cidfile or the PID of the disposable Docker/Podman client.
        ids = self._owned_containers()
        if not ids and self._container_id is None and self._launch_pending:
            raise OSError("container creation has unresolved identity; cleanup is unverified")
        if ids and self._container_id is None:
            self._container_id = ids[0]
        for container_id in ids:
            subprocess.run([self.executable, "rm", "--force", container_id],
                           env=self.launcher_env(), stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5, check=False)
        if self._owned_containers():
            raise OSError("owned container survived teardown")
        self._container_id = self._run_token = None
        self._launch_pending = False


def select_container_engine(raw: Any) -> Tuple[str, str, str]:
    """Shared, non-executing selection for provenance and actual launch.

    An inspect failure must not switch the manifest to a different engine
    from the one launch would select.
    """
    config = raw if isinstance(raw, dict) else {}
    requested = str(config.get("isolation_backend", "auto") or "auto").strip().lower()
    image = str(config.get("isolation_image", "") or "").strip()
    candidates = [requested] if requested in {"docker", "podman"} else ["docker", "podman"]
    for name in candidates:
        executable = shutil.which(name)
        if executable and image:
            return name, executable, image
    return "", "", image


def detect_isolation_backend(workspace: Path, raw: Any) -> Tuple[Optional[IsolationBackend], IsolationDescriptor]:
    """Resolve a concrete, available backend or return an unavailable record.

    ``raw`` is intentionally operator-controlled configuration.  A repository
    config can request a backend, but it cannot authorize an unconfined launch.
    """
    config = raw if isinstance(raw, dict) else {}
    requested = str(config.get("isolation_backend", "auto") or "auto").strip().lower()
    image = str(config.get("isolation_image", "") or "").strip()
    system = platform.system().lower()
    backend: IsolationBackend

    if requested in {"docker", "podman", "container"} or image:
        _name, container_executable, selected_image = select_container_engine(config)
        if container_executable:
            backend = ContainerBackend(container_executable, selected_image)
            return backend, backend.descriptor
        return None, IsolationDescriptor(
            backend="container", version="unavailable", available=False,
            network="unknown", filesystem="unknown",
            reason="configured container backend or image is unavailable",
        )

    if system == "linux" and requested in {"auto", "bubblewrap", "linux-bubblewrap"}:
        executable = shutil.which("bwrap")
        if executable and CgroupV2Controller.available():
            backend = LinuxBubblewrapBackend(executable)
            return backend, backend.descriptor
        return None, IsolationDescriptor(
            backend="linux-bubblewrap", version="unavailable", available=False,
            network="unknown", filesystem="unknown",
            reason=("bubblewrap or delegated writable cgroup v2 is unavailable; "
                    "refusing service start"),
        )

    return None, IsolationDescriptor(
        backend="none", version="unavailable", available=False,
        network="unsupported", filesystem="unsupported",
        reason=("no supported runtime-lab isolation backend for %s; "
                "Seatbelt/PoC isolation is not a target-service backend" % system),
    )


def capability_matrix() -> Dict[str, Any]:
    """Return a side-effect-free platform capability matrix for ``doctor``."""
    from .effects import supported_effect_kinds

    system = platform.system().lower()
    backends: Dict[str, Dict[str, Any]] = {}
    for name in ("bwrap", "docker", "podman", "nsenter"):
        path = shutil.which(name)
        backends[name] = {"available": bool(path), "path": path or ""}
    cgroup_available = CgroupV2Controller.available()
    backends["cgroup-v2"] = {"available": cgroup_available,
                              "path": str(CgroupV2Controller.ROOT)}
    return {
        "schema_version": ISOLATION_SCHEMA_VERSION,
        "platform": system,
        "poc_isolation": system == "darwin" and bool(shutil.which("sandbox-exec")),
        "service_isolation": bool((backends["bwrap"]["available"] and cgroup_available) or
                                   backends["docker"]["available"] or
                                   backends["podman"]["available"]),
        "http_observer": True,
        # This capability is deliberately limited to process lifecycle
        # observation. Target-specific protocol state is reported separately.
        "jvm_observer": "jvm-effect" in supported_effect_kinds(),
        "jvm_protocol_observer": "jvm-protocol" in supported_effect_kinds(),
        "effect_collectors": {
            kind: kind in supported_effect_kinds()
            for kind in ("http-semantic", "filesystem-diff", "process-effect",
                         "fixture-db", "jvm-effect", "jvm-protocol",
                         "authorization-state")
        },
        "backends": backends,
    }
