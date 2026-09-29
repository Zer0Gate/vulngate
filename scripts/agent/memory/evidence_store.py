"""Ordinary evidence projection and workspace-confined private file I/O.

Raw process output is not ordinary evidence. Keep it in memory for diagnosis,
never in these artifacts. This is not a raw vault or a general privacy detector:
free-form analyst text still needs review before publication.
"""

from __future__ import annotations

import json
import os
import re
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from ..tools.redaction import redact_text


STORAGE_POLICY = "ordinary-evidence-v1"
WITHHELD = "[WITHHELD: ordinary-evidence-v1]"
_RAW_FIELDS = frozenset({
    "stdout", "stderr", "compile_error", "harness_error", "raw_output",
    "raw_response", "request_body", "response_body", "cmd", "agent_reply",
})
_SECRET_FIELDS = frozenset({
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "access_token", "refresh_token", "authorization", "cookie", "set_cookie",
    "private_key", "client_secret",
})
_CHALLENGE_SCHEMAS = frozenset({
    "spawn-probe-challenge-v1", "parallel-receipt-challenge-v1", "parallel-receipt-v1",
})
_CELL_IDENTITY = frozenset({
    "candidate_id", "version", "safe_mode", "precondition", "features", "args",
    "authz_fixture_id", "cell_id",
})
_DIAGNOSTIC_LINE = re.compile(
    r"^(?P<label>\s*(?:HARNESS_ERROR|COMPILE_ERROR|POC_CLAIM_UNTRUSTED)\s*=).*$",
    re.DOTALL,
)
_GAP_CODES = frozenset({
    "authz-pass-not-closed-by-proxy-only-capture",
    "expected-http-code-not-independently-observed",
    "http-observer-unavailable", "java-network-observer-unavailable",
    "network-observer-target-unavailable", "no-proxied-response-captured",
    "os-network-isolation-policy-mismatch", "os-network-isolation-unavailable",
    "poc-scratch-unavailable", "target-url-invalid", "target-url-unavailable",
})
_SAFE_ERROR_TEXT = re.compile(
    r"completed receipt requires S4/matrix-runs/"
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}/cells\.json\Z"
)
_EFFECT_COLLECTIONS = frozenset({
    "observed_effects", "independent_effects", "independent_effect_evidence",
})
_SUMMARY_RAW_LISTS = frozenset({"network_side_effects", "parsed"})
_SUMMARY_RAW_FIELDS = {
    "instantiated": frozenset({"class"}),
    "gate_blocked": frozenset({"class"}),
    "safe_equivalent": frozenset({"kind", "detail"}),
    "effect_evidence": frozenset({"kind", "detail"}),
    "experiment_evidence": frozenset({
        "declared_sequence", "step_trace", "step_evidence", "state_trace", "warnings",
    }),
}


class EvidencePolicyError(ValueError):
    """A safe projection cannot preserve a required operational contract."""


def _withheld(value: Any) -> Any:
    # Preserve absent/empty error semantics and boolean capability indicators.
    if value is None or isinstance(value, bool) or not value:
        return value
    return WITHHELD


def _claims(value: Any) -> Any:
    if isinstance(value, list):
        return [_claims(row) for row in value]
    if not isinstance(value, dict):
        return _withheld(value)
    result = {}
    for key, item in value.items():
        if key == "fields" and isinstance(item, dict):
            # Keys may originate in legacy, attacker-authored observations too.
            # Retain the number of fields, not arbitrary marker names/values.
            result[key] = {
                "claim-%d" % index: ([_withheld(v) for v in values]
                                    if isinstance(values, list) else _withheld(values))
                for index, values in enumerate(item.values(), 1)
            }
        else:
            result[key] = public_document(item)
    return result


def public_document(value: Any, *, _parent: str = "") -> Any:
    """Project a copy, preserving verdict fields and never mutating live data.

    Omission of raw channels is structural, not dependent on recognizing a
    credential. Text redaction is an additional best-effort layer, not proof
    that arbitrary source code, business text or identifiers are public.
    """
    if isinstance(value, dict):
        result = {}
        is_cell = "observations" in value and "candidate_id" in value
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("evidence object keys must be strings")
            normalized = key.lower().replace("-", "_")
            if key == "token" and value.get("schema_version") in _CHALLENGE_SCHEMAS:
                # These non-secret challenge nonces must survive receipt replay.
                result[key] = item
            elif is_cell and key in _CELL_IDENTITY:
                projected = public_document(item)
                if projected != item:
                    raise EvidencePolicyError("sensitive cell identity requires a new run contract")
                result[key] = projected
            elif is_cell and key == "observations":
                from ..tools.build import _trusted_observations
                if _trusted_observations(value):
                    # Do not copy arbitrary extra response fields accepted by
                    # the observer validator into ordinary storage.
                    response_keys = {"kind", "method", "request_id", "status",
                                     "response_bytes", "body_complete", "response_truncated",
                                     "request_digest", "response_body_sha256"}
                    safe_observations = {
                        "HTTP_RESPONSES": [
                            {k: v for k, v in row.items() if k in response_keys}
                            for row in item.get("HTTP_RESPONSES", [])],
                    }
                    if "HTTP_CODE" in item:
                        safe_observations["HTTP_CODE"] = item["HTTP_CODE"]
                    if "HTTP_PREDICATES" in item:
                        safe_observations["HTTP_PREDICATES"] = [
                            {"id": public_document(row["id"]),
                             "matched": row["matched"],
                             **({"error": _withheld(row["error"])}
                                if "error" in row else {})}
                            for row in item["HTTP_PREDICATES"]]
                    result[key] = safe_observations
                else:
                    result[key] = _claims({"fields": item}).get("fields", {})
            elif normalized in {"observer_gaps", "observation_gaps"} and isinstance(item, list):
                # Two fixed gap codes affect classification. Preserve only
                # registered codes; parser/OS exception strings stay private.
                result[key] = [gap if isinstance(gap, str) and gap in _GAP_CODES
                               else _withheld(gap) for gap in item]
            elif normalized == "details" and _parent in _EFFECT_COLLECTIONS:
                # Effect status/kind/value_digest are harness metadata. Details
                # may contain filenames, object paths or exception text.
                result[key] = {}
            elif normalized in _SUMMARY_RAW_LISTS and isinstance(item, list):
                result[key] = [_withheld(entry) for entry in item]
            elif normalized in _SUMMARY_RAW_FIELDS.get(_parent, ()):
                if isinstance(item, list):
                    result[key] = [_withheld(entry) for entry in item]
                else:
                    result[key] = _withheld(item)
            elif normalized == "error":
                # Exception messages can embed arbitrary filenames, input and
                # service replies. Status/error_type carry the failure state.
                result[key] = (item if isinstance(item, str)
                               and _SAFE_ERROR_TEXT.fullmatch(item) else _withheld(item))
            elif (normalized == "reason"
                  and value.get("schema_version") == "runtime-lab-v1"
                  and value.get("status") == "run-failed"):
                result[key] = _withheld(item)
            elif ((normalized == "leaked" and _parent == "leaked")
                  or normalized in _RAW_FIELDS or normalized in _SECRET_FIELDS):
                result[key] = _withheld(item)
            elif normalized == "poc_claims":
                result[key] = _claims(item)
            else:
                result[key] = public_document(item, _parent=normalized)
        return result
    if isinstance(value, (tuple, list)):
        return [public_document(item, _parent=_parent) for item in value]
    if isinstance(value, str):
        # S4 ledger/report producers also render diagnostics into lines. Do
        # not let these preformatted copies bypass the structured field rule.
        diagnostic = _DIAGNOSTIC_LINE.fullmatch(value)
        if diagnostic is not None:
            return diagnostic.group("label") + WITHHELD
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    # No default=str: reprs of arbitrary runtime objects can contain secrets.
    raise TypeError("unsupported evidence value type")


def public_json(value: Any) -> str:
    return json.dumps(public_document(value), ensure_ascii=False, indent=2,
                      allow_nan=False)


class EvidenceStore:
    """POSIX descriptor-relative writes beneath an operator-selected workspace.

    Descendants must not be symlinks. Open directory descriptors avoid redirecting
    a write by replacing a checked parent with a symlink. This is not protection
    against a process with the same UID/root or a substitute for PoC isolation.
    """

    def __init__(self, workspace: Path):
        # The caller-selected workspace may use the normal macOS /var alias.
        self.root = Path(workspace).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _parts(relative: str | Path) -> tuple[str, ...]:
        text = str(relative)
        parts = PurePosixPath(text).parts
        if (not text or "\\" in text or text.startswith("/")
                or any(part in {"", ".", ".."} for part in text.split("/"))):
            raise ValueError("evidence path must be workspace-relative")
        return parts

    @contextmanager
    def _directory(self, parts: tuple[str, ...], *, create: bool) -> Iterator[int]:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(self.root, flags)
        try:
            for part in parts:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
                if create:
                    os.fchmod(fd, 0o700)
            yield fd
        finally:
            os.close(fd)

    def directory(self, relative: str | Path) -> Path:
        parts = self._parts(relative)
        with self._directory(parts, create=True):
            pass
        return self.root.joinpath(*parts)

    def _write(self, relative: str | Path, content: str) -> Path:
        parts = self._parts(relative)
        with self._directory(parts[:-1], create=True) as parent:
            try:
                current = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                current = None
            if current is not None and not stat.S_ISREG(current.st_mode):
                raise ValueError("evidence destination must be a regular file")
            temp = ".evidence-%s.tmp" % uuid.uuid4().hex
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                try:
                    os.unlink(temp, dir_fd=parent)
                except FileNotFoundError:
                    pass
        return self.root.joinpath(*parts)

    def write_json(self, relative: str | Path, data: Any) -> Path:
        return self._write(relative, public_json(data))

    def write_text(self, relative: str | Path, text: str) -> Path:
        # A serialized JSON document must not bypass the structured policy.
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            content = redact_text(text)
        else:
            content = public_json(data)
        return self._write(relative, content)

    def read_text(self, relative: str | Path) -> str:
        parts = self._parts(relative)
        with self._directory(parts[:-1], create=False) as parent:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=parent)
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("evidence source must be a regular file")
                return stream.read()
