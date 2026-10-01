"""Checkpoint store: stage state files + artifacts for breakpoint resume."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .evidence_store import EvidenceStore


class CheckpointStore:
    def __init__(self, workspace: Path, target: str, round_no: int):
        self.workspace = workspace.resolve()
        self._input_workspace = workspace
        self._store = EvidenceStore(workspace)
        self.target = target
        self.round_no = round_no
        # Set only by the execution controller after a full current-identity
        # comparison. Plain stores retain legacy inspection compatibility.
        from .artifact_identity import current_identity
        active = current_identity(self.workspace)
        self._manifest_sha256: Optional[str] = None
        if active is not None and (active.target, active.round_no) == (target, round_no):
            self.manifest_sha256 = active.manifest_sha256
        self.base = workspace / "state" / target / ("round-%02d" % round_no)
        self._store.directory(self.base.relative_to(workspace))

    def stage_file(self, stage: str) -> Path:
        if not stage or "/" in stage or "\\" in stage or stage in {".", ".."}:
            raise ValueError("stage must be one path component")
        return self.base / ("stage-%s.json" % stage)

    @property
    def manifest_sha256(self) -> Optional[str]:
        return self._manifest_sha256

    @manifest_sha256.setter
    def manifest_sha256(self, value: Optional[str]) -> None:
        from .artifact_identity import ArtifactIdentity
        self._manifest_sha256 = value
        self._store.binding = (ArtifactIdentity(self.workspace, self.target, self.round_no, value)
                               if value is not None else None)

    def load_stage(self, stage: str) -> Optional[Dict[str, Any]]:
        from ..orchestrator.run_identity import RunIdentityError
        f = self.stage_file(stage)
        try:
            data = json.loads(self._store.read_text(f.relative_to(self._input_workspace)))
        except FileNotFoundError:
            return None
        except RunIdentityError as exc:
            raise RunIdentityError("checkpoint byte binding rejected: " + stage) from exc
        if (self.manifest_sha256 is not None and stage != "S0"
                and (not isinstance(data, dict)
                     or data.get("manifest_sha256") != self.manifest_sha256)):
            raise RunIdentityError("checkpoint is not bound to current run identity: " + stage)
        if self.manifest_sha256 is not None and isinstance(data, dict):
            if stage == "S7":
                names = data.get("finding_docs", [])
                if not isinstance(names, list):
                    raise RunIdentityError("invalid bound report list")
                for name in names:
                    if not isinstance(name, str) or Path(name).name != name:
                        raise RunIdentityError("invalid bound report reference")
                    relative = Path("reports") / self.target / ("round-%02d" % self.round_no) / name
                    try:
                        self._store.read_bytes(relative)
                    except OSError as exc:
                        raise RunIdentityError("bound report is missing or unsafe") from exc
            if stage == "S8" and data.get("ledger_dir"):
                directory = Path("ledger") / self.target / ("round-%02d" % self.round_no)
                if data["ledger_dir"] != str(directory):
                    raise RunIdentityError("invalid bound ledger reference")
                try:
                    publication = json.loads(self._store.read_text(directory / "publication-files.json"))
                    names = publication.get("files") if isinstance(publication, dict) else None
                    if (not isinstance(names, list) or not all(isinstance(name, str) for name in names)
                            or "ledger.json" not in names or len(names) != 4 or len(set(names)) != 4):
                        raise RunIdentityError("invalid ledger publication manifest")
                    for name in names:
                        if not isinstance(name, str) or Path(name).name != name:
                            raise RunIdentityError("invalid bound ledger file reference")
                        self._store.read_bytes(directory / name)
                except OSError as exc:
                    raise RunIdentityError("bound ledger is missing or unsafe") from exc
        return data

    def save_stage(self, stage: str, data: Dict[str, Any]) -> Path:
        data.setdefault("stage", stage)
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        if self.manifest_sha256 is not None:
            data["manifest_sha256"] = self.manifest_sha256
        f = self.stage_file(stage)
        self._atomic_write_json(f, data)
        return f

    def completed_stages(self) -> List[str]:
        stages = []
        for f in sorted(self.base.glob("stage-S*.json")):
            stages.append(f.stem.split("-", 1)[1])
        return stages

    def artifact_path(self, stage: str, name: str) -> Path:
        self.stage_file(stage)
        self._store._parts(name)
        d = self.base / stage
        self._store.directory(d.relative_to(self._input_workspace))
        return d / name

    def write_artifact(self, stage: str, name: str, data: Any) -> Path:
        f = self.artifact_path(stage, name)
        if isinstance(data, (dict, list)):
            self._atomic_write_json(f, data)
        else:
            self._atomic_write(f, str(data))
        return f

    def _atomic_write_json(self, path: Path, data: Any) -> None:
        self._store.write_json(path.relative_to(self._input_workspace), data)

    def _atomic_write(self, path: Path, content: str) -> None:
        self._store.write_text(path.relative_to(self._input_workspace), content)

    def read_artifact(self, stage: str, name: str) -> Any:
        # Inspection is genuinely non-creating, including missing stage dirs.
        self.stage_file(stage)
        self._store._parts(name)
        f = self.base / stage / name
        try:
            text = self._store.read_text(f.relative_to(self._input_workspace))
        except FileNotFoundError:
            return None
        if f.suffix == ".json":
            return json.loads(text)
        return text

    def approval_log(self) -> Path:
        return self.base / "approval-log.jsonl"
