"""Explicit, bounded retention for S4 raw diagnostics.

No vault is created unless the operator opts in. This is a local OS-identity
boundary, not protection from other code running with the operator's UID.
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from .evidence_store import EvidenceStore


VAULT_POLICY = "s4-raw-vault-v1"
MAX_RECORD_BYTES = 32 * 1024 * 1024
MAX_STORED_BYTES = 48 * 1024 * 1024
_RECORD_NAME = re.compile(r"^(\d{10})-(\d{10})-([0-9a-f]{32})\.vault$")


class RawVault:
    """Private raw S4 records with expiry and optional authenticated encryption."""

    def __init__(self, workspace: Path, target: str, round_no: int, *,
                 mode: str, retention_days: int = 7,
                 key_file: Optional[Path] = None,
                 clock: Callable[[], float] = time.time):
        if mode not in {"plain", "fernet"}:
            raise ValueError("raw vault mode must be plain or fernet")
        if type(retention_days) is not int or not 1 <= retention_days <= 30:
            raise ValueError("raw vault retention must be 1..30 days")
        if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", target):
            raise ValueError("raw vault target must be a path-safe component")
        if type(round_no) is not int or round_no < 1:
            raise ValueError("raw vault round must be positive")
        if mode == "plain" and key_file is not None:
            raise ValueError("plain raw vault cannot accept an encryption key")
        if mode == "fernet" and key_file is None:
            raise ValueError("fernet raw vault requires a private key file")
        self.store = EvidenceStore(workspace)
        self.directory = Path("state") / target / ("round-%02d" % round_no) / "S4/raw-vault"
        self.mode = mode
        self.retention_seconds = retention_days * 86400
        self.clock = clock
        self._cipher = None
        if mode == "fernet":
            assert key_file is not None
            try:
                from cryptography.fernet import Fernet
            except ImportError as exc:
                raise RuntimeError("encrypted raw vault requires cryptography") from exc
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            fd = os.open(Path(key_file), flags)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode)
                        or stat.S_IMODE(info.st_mode) != 0o600
                        or info.st_uid != os.getuid()):
                    raise PermissionError("raw vault key must be an owned 0600 regular file")
                key = stream.read(65).strip()
            if len(key) != 44:
                raise ValueError("raw vault encryption key format is invalid")
            self._cipher = Fernet(key)

    @classmethod
    def from_environment(cls, workspace: Path, target: str,
                         round_no: int) -> Optional["RawVault"]:
        mode = os.environ.get("VULNGATE_RAW_VAULT", "off").strip().lower()
        if mode == "off":
            return None
        if mode not in {"plain", "fernet"}:
            raise ValueError("VULNGATE_RAW_VAULT must be off, plain or fernet")
        days_text = os.environ.get("VULNGATE_RAW_VAULT_DAYS", "7")
        if not days_text.isdecimal():
            raise ValueError("VULNGATE_RAW_VAULT_DAYS must be 1..30")
        key_text = os.environ.get("VULNGATE_RAW_VAULT_KEY_FILE", "")
        return cls(workspace, target, round_no, mode=mode,
                   retention_days=int(days_text),
                   key_file=Path(key_text) if key_text else None)

    def _record(self, name: str) -> tuple[int, int]:
        match = _RECORD_NAME.fullmatch(name)
        if match is None:
            raise ValueError("invalid raw vault record name")
        created, expires = int(match.group(1)), int(match.group(2))
        if not created < expires <= created + 30 * 86400:
            raise ValueError("invalid raw vault expiry")
        return created, expires

    def _encode(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
        if len(raw) > MAX_RECORD_BYTES:
            raise ValueError("raw vault record exceeds bounded size")
        if self._cipher is None:
            return raw.decode("utf-8")
        return self._cipher.encrypt(raw).decode("ascii")

    def capture_cells(self, candidate_id: str, cells: list[dict[str, Any]]) -> str:
        if not isinstance(candidate_id, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", candidate_id):
            raise ValueError("raw vault candidate must be path-safe")
        now = int(self.clock())
        self.purge_expired()
        expires = now + self.retention_seconds
        record_name = "%010d-%010d-%s.vault" % (now, expires, uuid.uuid4().hex)
        payload = {"schema_version": VAULT_POLICY, "created_at": now,
                   "expires_at": expires,
                   "candidate_id": candidate_id, "cells": cells}
        content = self._encode(payload)
        self.store._write(self.directory / record_name, content)
        return record_name

    def read(self, name: str) -> dict[str, Any]:
        created, expires = self._record(name)
        if expires <= int(self.clock()):
            raise ValueError("raw vault record expired")
        parts = self.store._parts(self.directory / name)
        with self.store._directory(parts[:-1], create=False) as parent:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=parent)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("raw vault record must be a regular file")
                if info.st_size > MAX_STORED_BYTES:
                    raise ValueError("raw vault record exceeds bounded size")
                data = stream.read(MAX_STORED_BYTES + 1)
                if len(data) > MAX_STORED_BYTES:
                    raise ValueError("raw vault record exceeds bounded size")
        if self._cipher is not None:
            from cryptography.fernet import InvalidToken
            try:
                data = self._cipher.decrypt(data)
            except InvalidToken as exc:
                raise ValueError("raw vault authentication failed") from exc
        payload = json.loads(data.decode("utf-8"))
        if (not isinstance(payload, dict) or payload.get("schema_version") != VAULT_POLICY
                or payload.get("created_at") != created
                or payload.get("expires_at") != expires
                or not isinstance(payload.get("cells"), list)):
            raise ValueError("raw vault record identity mismatch")
        return payload

    def purge_expired(self) -> int:
        # Only own *.vault records in this one target/round vault are eligible.
        removed = 0
        try:
            with self.store._directory(self.store._parts(self.directory), create=False) as fd:
                for name in os.listdir(fd):
                    match = _RECORD_NAME.fullmatch(name)
                    if match is None:
                        continue
                    _, expires = self._record(name)
                    if expires > int(self.clock()):
                        continue
                    try:
                        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        # Another authorized cleaner may have removed this record.
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError("raw vault contains a non-regular record")
                    try:
                        os.unlink(name, dir_fd=fd)
                    except FileNotFoundError:
                        continue
                    removed += 1
                if removed:
                    os.fsync(fd)
        except FileNotFoundError:
            return 0
        return removed

    @classmethod
    def purge_expired_scope(cls, workspace: Path, target: str, round_no: int) -> int:
        """Delete expired names without requiring a lost encryption key."""
        if not Path(workspace).is_dir():
            raise ValueError("raw vault workspace does not exist")
        return cls(workspace, target, round_no, mode="plain").purge_expired()
