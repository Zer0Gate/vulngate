#!/usr/bin/env python3
"""Recoverable deployment with retained content-addressed generations.

Locks serialize installers. A durable journal rolls back interrupted filesystem
publication. Codex activation is verified separately; it is not part of the
filesystem transaction and failed activation leaves its cache state unknown.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack, contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from agent.tools.tool_identity import IDENTITY_FILE, PAYLOAD, tree_identity, verify_tree


def sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".vulngate-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_dir(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def json_bytes(data) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def atomic_link(path: Path, target: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".vulngate-link-" + uuid.uuid4().hex)
    try:
        temporary.symlink_to(target, target_is_directory=True)
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def locked(path: Path, timeout: float = 30):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("another installation holds " + str(path))
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def snapshot(path: Path) -> dict:
    if path.is_symlink():
        return {"kind": "link", "target": os.readlink(path)}
    if path.is_file():
        return {"kind": "file", "bytes": base64.b64encode(path.read_bytes()).decode()}
    if path.is_dir():
        return {"kind": "directory"}
    if path.exists():
        raise ValueError("unsupported install destination: " + str(path))
    return {"kind": "absent"}


def manifest(root: Path) -> dict:
    data = json.loads((root / ".codex-plugin/plugin.json").read_text())
    if data.get("name") != "vulngate" or not re.fullmatch(
            r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.+-]+)?", str(data.get("version", ""))):
        raise ValueError("invalid plugin name/version")
    for field in ("description", "skills", "interface"):
        if not data.get(field):
            raise ValueError("missing plugin field " + field)
    for field in ("composerIcon", "logo", "logoDark"):
        value = data["interface"].get(field)
        if value:
            asset = (root / value).resolve()
            if not asset.is_relative_to(root.resolve()) or not asset.is_file():
                raise ValueError("invalid plugin asset " + field)
    return data


def stage_generation(source: Path, state: Path) -> Path:
    generations = state / "generations"
    if generations.is_symlink():
        raise ValueError("generation storage must not be a symlink")
    generations.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="staging-", dir=state))
    try:
        ignored = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
        for name in PAYLOAD:
            origin, dest = source / name, stage / name
            if origin.is_symlink():
                raise ValueError("payload root must not be a symlink: " + name)
            if origin.is_dir():
                shutil.copytree(origin, dest, symlinks=True, ignore=ignored)
            else:
                shutil.copy2(origin, dest)
        data = manifest(stage)
        identity = tree_identity(stage)
        atomic_bytes(stage / IDENTITY_FILE, json_bytes(identity))
        verify_tree(stage)
        for previous in generations.iterdir():
            if previous.is_dir() and manifest(previous)["version"] == data["version"]:
                if verify_tree(previous)["tree_sha256"] != identity["tree_sha256"]:
                    raise ValueError("plugin version already identifies different content; update the cachebuster first")
        generation = generations / identity["tree_sha256"]
        if generation.exists():
            verify_tree(generation)
            # A crash after rename but before chmod may leave the unpublished
            # generation root writable. Restore its mode before publishing it.
            generation.chmod(0o555)
            sync_dir(generation)
            return generation
        for path in stage.rglob("*"):
            if path.is_file():
                with path.open("rb") as stream:
                    os.fsync(stream.fileno())
                path.chmod(0o555 if path.stat().st_mode & 0o111 else 0o444)
        for path in sorted(stage.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_dir():
                sync_dir(path)
                path.chmod(0o555)
        sync_dir(stage)
        # Darwin needs a writable source directory when moving it between
        # parents (the directory's '..' entry changes). No public pointer exists
        # yet; freeze its root only after this private generation is renamed.
        os.replace(stage, generation)
        generation.chmod(0o555)
        sync_dir(generation)
        sync_dir(generations)
        sync_dir(state)
        return generation
    finally:
        if stage.exists():
            # Only this operation's tempfile is removed, never a published tree.
            for path in [stage, *stage.rglob("*")]:
                if not path.is_symlink():
                    path.chmod(0o700 if path.is_dir() else 0o600)
            shutil.rmtree(stage)


def marketplace_payload(path: Path) -> bytes:
    data = json.loads(path.read_text()) if path.exists() else {}
    name = data.setdefault("name", "personal")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("invalid marketplace name")
    data.setdefault("interface", {}).setdefault("displayName", "Personal")
    plugins = data.setdefault("plugins", [])
    matches = [i for i, item in enumerate(plugins) if item.get("name") == "vulngate"]
    if len(matches) > 1:
        raise ValueError("duplicate vulngate marketplace entries")
    entry = {"name": "vulngate", "source": {"source": "local", "path": "./plugins/vulngate"},
             "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
             "category": "Engineering"}
    if matches:
        plugins[matches[0]] = entry
    else:
        plugins.append(entry)
    return json_bytes(data)


def recover(state: Path, dest: Path, market: Path, source_link: Path) -> None:
    journal = state / "transaction.json"
    if not journal.exists():
        return
    record = json.loads(journal.read_text())
    allowed = {str(dest), str(market), str(source_link)}
    if record.get("schema") != "vulngate-install-v1":
        raise ValueError("unknown install journal")
    operations = record["operations"]
    if any(op["path"] not in allowed for op in operations):
        raise ValueError("install journal does not match requested destinations")
    if record.get("committed"):
        if any(snapshot(Path(op["path"])) != op["after"] for op in operations):
            raise RuntimeError("committed installation changed before recovery")
        atomic_bytes(state / "receipt.json", json_bytes(record["receipt"]))
        journal.unlink()
        sync_dir(state)
        return
    for operation in reversed(operations):
        path = Path(operation["path"])
        old, new = operation["before"], operation["after"]
        current = snapshot(path)
        if current == old:
            continue
        if current != new and not (current["kind"] == "absent" and old["kind"] == "directory"):
            raise RuntimeError("concurrent change prevents safe rollback: " + str(path))
        if old["kind"] == "directory":
            backup = Path(operation["backup"])
            if backup.parent != state or not re.fullmatch(r"legacy-[a-f0-9]{32}", backup.name):
                raise ValueError("invalid legacy backup")
            if not backup.is_dir() or backup.is_symlink():
                raise RuntimeError("legacy backup unavailable")
            if path.is_symlink():
                path.unlink()
            os.replace(backup, path)
        elif old["kind"] == "link":
            atomic_link(path, old["target"])
        elif old["kind"] == "file":
            atomic_bytes(path, base64.b64decode(old["bytes"], validate=True))
        else:
            path.unlink(missing_ok=True)
        sync_dir(path.parent)
    atomic_bytes(state / "last-recovery.json", json_bytes({
        "filesystem_restored": True,
        "activation_status": "unknown" if record.get("activation_started") else "not-started"}))
    journal.unlink()
    sync_dir(state)


def find_codex() -> str | None:
    override = os.environ.get("CODEX_BIN")
    if override:
        if not os.access(override, os.X_OK):
            raise ValueError("CODEX_BIN is not executable")
        return override
    return shutil.which("codex") or next((str(path) for path in (
        Path("/Applications/Codex.app/Contents/Resources/codex"),
        Path("/Applications/ChatGPT.app/Contents/Resources/codex")) if path.is_file()), None)


def install(source: Path, dest: Path, market: Path, *, enable: bool) -> dict:
    # Resolve parent aliases without dereferencing the current-version link.
    dest = dest.parent.resolve() / dest.name
    market = market.parent.resolve() / market.name
    if dest.name != "vulngate" or dest == source.resolve():
        raise ValueError("invalid or source-overwriting install destination")
    state = dest.parent / ".vulngate-install"
    if state.is_symlink():
        raise ValueError("install state must not be a symlink")
    if market.is_relative_to(dest) or market.is_relative_to(state):
        raise ValueError("marketplace must be outside installation and state trees")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    source_link = market.parent / "plugins/vulngate"
    locks = sorted({state / "install.lock", market.parent / ".vulngate-marketplace.lock"})
    with ExitStack() as stack:
        for lock in locks:
            stack.enter_context(locked(lock))
        recover(state, dest, market, source_link)
        generation = stage_generation(source, state)
        payload = marketplace_payload(market)
        operations = []
        links = [(dest, str(generation))]
        if source_link != dest:
            links.append((source_link, str(dest)))
        for path, target in links:
            before = snapshot(path)
            if before["kind"] == "file" or (path != dest and before["kind"] == "directory"):
                raise ValueError("refusing to replace non-plugin path: " + str(path))
            operations.append({"path": str(path), "before": before,
                               "after": {"kind": "link", "target": target},
                               "backup": str(state / ("legacy-" + uuid.uuid4().hex))})
        before = snapshot(market)
        if before["kind"] not in {"absent", "file"}:
            raise ValueError("marketplace must be a regular file")
        operations.append({"path": str(market), "before": before,
                           "after": {"kind": "file", "bytes": base64.b64encode(payload).decode()}})
        record = {"schema": "vulngate-install-v1", "operations": operations,
                  "activation_started": False, "committed": False}
        journal = state / "transaction.json"
        atomic_bytes(journal, json_bytes(record))
        try:
            for operation in operations:
                path = Path(operation["path"])
                if snapshot(path) != operation["before"]:
                    raise RuntimeError("destination changed during installation")
                if operation["before"]["kind"] == "directory":
                    os.replace(path, operation["backup"])
                    sync_dir(path.parent)
                    sync_dir(state)
                if operation["after"]["kind"] == "link":
                    atomic_link(path, operation["after"]["target"])
                else:
                    atomic_bytes(path, payload)
            identity = verify_tree(dest)
            activation = "not-requested"
            if enable:
                codex = find_codex()
                if not codex:
                    raise RuntimeError("Codex CLI unavailable; use --no-enable for source installation only")
                record["activation_started"] = True
                atomic_bytes(journal, json_bytes(record))
                name = json.loads(payload)["name"]
                result = subprocess.run([codex, "plugin", "add", "vulngate@" + name],
                                        capture_output=True, text=True, timeout=180, check=True)
                match = re.search(r"^Installed plugin root: (.+)$", result.stdout, re.MULTILINE)
                if not match or verify_tree(Path(match[1])) != identity:
                    raise RuntimeError("Codex cache installation did not verify against the generation")
                activation = "verified"
            receipt = {"schema": "vulngate-install-receipt-v1",
                       "version": manifest(dest)["version"], "generation": str(generation),
                       "tree_sha256": identity["tree_sha256"], "activation": activation,
                       "old_generations_retained": True}
            record["receipt"] = receipt
            record["committed"] = True
            atomic_bytes(journal, json_bytes(record))
            recover(state, dest, market, source_link)
            return receipt
        except BaseException:
            recover(state, dest, market, source_link)
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--no-enable", action="store_true")
    args = parser.parse_args()

    def interrupted(signum, _frame):
        raise RuntimeError("installation interrupted by signal %d" % signum)

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        result = install(args.source.resolve(),
                         Path(os.environ.get("PLUGIN_HOME", str(Path.home() / "plugins"))) / "vulngate",
                         Path(os.environ.get("VULNGATE_MARKETPLACE", str(Path.home() / ".agents/plugins/marketplace.json"))),
                         enable=not args.no_enable)
        print(json.dumps(result, indent=2))
        print("Use a new Codex thread. Missing old caches stop old threads; no version aliases are created.")
        return 0
    except subprocess.CalledProcessError as exc:
        print("Codex activation failed; filesystem restored; Codex cache state must be rechecked.", file=sys.stderr)
        return exc.returncode if 0 < exc.returncode < 126 else 2
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print("Installation failed: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
