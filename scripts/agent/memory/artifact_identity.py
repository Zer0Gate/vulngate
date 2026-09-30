"""Exact-byte run binding, not observer authentication or an execution attestation.

Only a freshly compared RunManifest activates the execution scope. Historical
inspection has no active scope. Publication records preserve public payloads;
an interrupted two-file publication fails closed instead of blessing old bytes.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, Iterator, NoReturn


ACTIVE: ContextVar[ArtifactIdentity | None] = ContextVar("artifact_identity", default=None)
SCOPED: ContextVar[bool] = ContextVar("artifact_identity_scoped", default=False)


def refuse(message: str) -> NoReturn:
    from ..orchestrator.run_identity import RunIdentityError
    raise RunIdentityError(message)


@dataclass(frozen=True)
class ArtifactIdentity:
    workspace: Path
    target: str
    round_no: int
    manifest_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", self.workspace.resolve(strict=True))
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", self.target)
                or type(self.round_no) is not int or self.round_no < 1):
            refuse("invalid artifact target/round identity")
        if not re.fullmatch(r"[0-9a-f]{64}", self.manifest_sha256):
            refuse("invalid artifact parent digest")

    @property
    def base(self) -> Path:
        return Path("state") / self.target / ("round-%02d" % self.round_no)

    def covers(self, relative: Path) -> bool:
        for category in ("reports", "ledger"):
            if relative.is_relative_to(Path(category) / self.target / ("round-%02d" % self.round_no)):
                return True
        if not relative.is_relative_to(self.base):
            return False
        parts = relative.relative_to(self.base).parts
        # S0 diagnostics, deadlines and binding records are not execution data.
        return bool(parts and (parts[0] != "S0") and (
            re.fullmatch(r"stage-S[1-8]\.json", parts[0])
            or re.fullmatch(r"S[1-8]|FUZZ", parts[0])))

    def record_path(self, relative: Path) -> Path:
        key = hashlib.sha256(relative.as_posix().encode("utf-8")).hexdigest()
        return self.base / "S0" / "artifact-bindings" / (key + ".json")

    def record(self, store: Any, relative: Path, content: bytes) -> None:
        store.write_json(self.record_path(relative), {
            "schema_version": "run-artifact-binding-v1",
            "manifest_sha256": self.manifest_sha256,
            "path": relative.as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(), "size": len(content),
            "claim_status": "not-a-finding",
        })

    def expected_record(self, store: Any, relative: Path) -> dict[str, Any]:
        try:
            record = json.loads(store.read_bytes(self.record_path(relative), max_bytes=16384))
        except (OSError, ValueError, UnicodeError):
            refuse("artifact binding unavailable: " + relative.as_posix())
        if (not isinstance(record, dict) or type(record.get("size")) is not int
                or record["size"] < 0 or record.get("manifest_sha256") != self.manifest_sha256
                or record.get("path") != relative.as_posix()
                or not isinstance(record.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])):
            refuse("invalid artifact binding: " + relative.as_posix())
        return record

    def verify(self, relative: Path, content: bytes, record: dict[str, Any]) -> None:
        expected = {"schema_version": "run-artifact-binding-v1",
                    "manifest_sha256": self.manifest_sha256,
                    "path": relative.as_posix(),
                    "sha256": hashlib.sha256(content).hexdigest(), "size": len(content),
                    "claim_status": "not-a-finding"}
        if json.dumps(record, sort_keys=True) != json.dumps(expected, sort_keys=True):
            refuse("artifact does not match current run bytes: " + relative.as_posix())


def current_identity(workspace: Path) -> ArtifactIdentity | None:
    active = ACTIVE.get()
    return active if active is not None and active.workspace == workspace.resolve() else None


def activate(identity: ArtifactIdentity) -> None:
    if SCOPED.get():
        ACTIVE.set(identity)


@contextmanager
def identity_scope() -> Iterator[None]:
    token = ACTIVE.set(None)
    scoped = SCOPED.set(True)
    try:
        yield
    finally:
        ACTIVE.reset(token)
        SCOPED.reset(scoped)


def isolated_identity(function: Any) -> Any:
    @wraps(function)
    def run(*args: Any, **kwargs: Any) -> Any:
        with identity_scope():
            return function(*args, **kwargs)
    return run
