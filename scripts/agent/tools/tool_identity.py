"""Source-tree integrity shared by deployment and run provenance.

The digest authenticates content identity, not publisher identity. Installed
trees are read-only, and targets must not have write access to the trusted tree.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..orchestrator.work_budget import WorkBudget

IDENTITY_FILE = ".codex-plugin/content-manifest.json"
PAYLOAD = (
    ".codex-plugin", "hooks", "skills", "scripts", "macos", "assets", "docs",
    "benchmarks", "schemas", "pyproject.toml", "README.md", "README.zh-CN.md",
    "LICENSE", "CHANGELOG.md", "PROVENANCE.md", "RELATED_WORK.md", "SECURITY.md",
    "SECURITY.zh-CN.md", "CONTRIBUTING.md", "CONTRIBUTING.zh-CN.md",
)


def tree_identity(root: Path, *, payload_only: bool = False,
                  budget: WorkBudget | None = None) -> dict[str, Any]:
    root = root.resolve(strict=True)
    rows = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        if budget is not None:
            budget.check()
        if payload_only and Path(directory) == root:
            dirs[:] = [name for name in dirs if name in PAYLOAD]
            files = [name for name in files if name in PAYLOAD]
        dirs[:] = sorted(d for d in dirs if d not in {"__pycache__", ".git"})
        for name in dirs + sorted(files):
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError("tool tree symlinks are not allowed: %s" % path.relative_to(root))
        for name in sorted(files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if relative == IDENTITY_FILE or name.endswith(".pyc") or name == ".DS_Store":
                continue
            if not path.is_file():
                raise ValueError("tool tree contains a non-regular file")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                if budget is not None:
                    budget.consume("scan_files")
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    if budget is not None:
                        budget.check()
                        budget.consume("scan_bytes", len(block))
                    digest.update(block)
            rows.append({"path": relative, "sha256": digest.hexdigest(),
                         "executable": bool(path.stat().st_mode & 0o111)})
    rows.sort(key=lambda row: row["path"])
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return {"schema_version": "vulngate-tool-tree-v1",
            "tree_sha256": hashlib.sha256(encoded).hexdigest(), "files": rows}


def runtime_tree_identity(root: Path, *, budget: WorkBudget | None = None) -> dict[str, Any]:
    """Installed generations must verify; checkouts hash the shipped payload.

    Never hash generated workspace state as tool code or silently fall back
    from a damaged installed generation to a development tree.
    """
    if (root / IDENTITY_FILE).exists() or (root / IDENTITY_FILE).is_symlink():
        return verify_tree(root, budget=budget)
    return tree_identity(root, payload_only=True, budget=budget)


def verify_tree(root: Path, *, budget: WorkBudget | None = None) -> dict[str, Any]:
    expected = json.loads((root / IDENTITY_FILE).read_text(encoding="utf-8"))
    actual = tree_identity(root, budget=budget)
    if expected != actual:
        raise ValueError("tool tree integrity mismatch")
    return actual
