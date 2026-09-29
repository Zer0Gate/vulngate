"""Source-tree integrity shared by deployment and run provenance.

The digest authenticates content identity, not publisher identity. Installed
trees are read-only, and targets must not have write access to the trusted tree.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

IDENTITY_FILE = ".codex-plugin/content-manifest.json"


def tree_identity(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    rows = []
    for directory, dirs, files in os.walk(root, followlinks=False):
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
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            rows.append({"path": relative, "sha256": digest.hexdigest(),
                         "executable": bool(path.stat().st_mode & 0o111)})
    rows.sort(key=lambda row: row["path"])
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return {"schema_version": "vulngate-tool-tree-v1",
            "tree_sha256": hashlib.sha256(encoded).hexdigest(), "files": rows}


def verify_tree(root: Path) -> dict[str, Any]:
    expected = json.loads((root / IDENTITY_FILE).read_text(encoding="utf-8"))
    actual = tree_identity(root)
    if expected != actual:
        raise ValueError("tool tree integrity mismatch")
    return actual
