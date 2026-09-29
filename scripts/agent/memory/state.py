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
        self.base = workspace / "state" / target / ("round-%02d" % round_no)
        self._store.directory(self.base.relative_to(workspace))

    def stage_file(self, stage: str) -> Path:
        if not stage or "/" in stage or "\\" in stage or stage in {".", ".."}:
            raise ValueError("stage must be one path component")
        return self.base / ("stage-%s.json" % stage)

    def load_stage(self, stage: str) -> Optional[Dict[str, Any]]:
        f = self.stage_file(stage)
        try:
            return json.loads(self._store.read_text(f.relative_to(self._input_workspace)))
        except FileNotFoundError:
            return None

    def save_stage(self, stage: str, data: Dict[str, Any]) -> Path:
        data.setdefault("stage", stage)
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
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
        f = self.artifact_path(stage, name)
        try:
            text = self._store.read_text(f.relative_to(self._input_workspace))
        except FileNotFoundError:
            return None
        if f.suffix == ".json":
            return json.loads(text)
        return text

    def approval_log(self) -> Path:
        return self.base / "approval-log.jsonl"
