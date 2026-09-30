"""Create-once round identity for checkpoint reuse, not an evidence attestation.

Only digests and non-secret version identifiers are retained. A matching record
does not authenticate target-authored cells or freeze mutable inputs during
execution; those boundaries require their own provenance and isolation checks.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..analysis.languages import resolve_source_dirs
from ..memory.evidence_store import EvidenceStore, public_document
from ..tools.tool_identity import runtime_tree_identity
from .config import TargetConfig
from .work_budget import WorkBudget

SCHEMA_VERSION = "vulngate-run-manifest-v1"
MANIFEST_NAME = "S0/run-manifest.json"
# Controller-owned outputs are not target inputs. This exclusion applies only
# to implicit workspace-root audits, never to a configured source directory.
WORKSPACE_OUTPUTS = frozenset({"state", "ledger", "reports", "poc"})


class RunIdentityError(ValueError):
    """Refuse unsafe reuse without replacing the original run identity."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _probe_timeout(budget: WorkBudget | None) -> float:
    if budget is None:
        return 5.0
    budget.check()
    remaining = budget.remaining("wall_seconds")
    return min(5.0, remaining) if remaining is not None else 5.0


def _revision(root: Path, budget: WorkBudget | None = None) -> str:
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
                                capture_output=True, text=True, timeout=_probe_timeout(budget), check=False)
    except subprocess.TimeoutExpired as exc:
        raise RunIdentityError("revision probe exceeded round budget") from exc
    value = result.stdout.strip()
    return value if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", value) else "non-git"


def _snapshot(root: Path, *, excluded: frozenset[str] = frozenset(),
              budget: WorkBudget | None = None) -> str:
    """Hash bytes, paths and executable bits; never trust size/mtime or Git OID.

    Directory-fd traversal refuses symlinks and special files before reading.
    The budget is shared with the round: hashing cannot mint a new scan window.
    """
    rows: list[Any] = []

    def visit(fd: int, relative: str) -> None:
        if budget is not None:
            budget.check()
        with os.scandir(fd) as entries:
            names = sorted(entry.name for entry in entries)
        for name in names:
            if name == ".git" or (not relative and name in excluded):
                continue
            if budget is not None:
                budget.check()
                budget.consume("scan_files")
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            path = relative + "/" + name if relative else name
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    rows.append([path, "directory", bool(info.st_mode & 0o111)])
                    visit(child, path)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                try:
                    before = os.fstat(child)
                    if not stat.S_ISREG(before.st_mode) or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
                        raise RunIdentityError("input changed during identity collection")
                    content = hashlib.sha256()
                    while block := os.read(child, 1024 * 1024):
                        if budget is not None:
                            budget.check()
                            budget.consume("scan_bytes", len(block))
                        content.update(block)
                    after = os.fstat(child)
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise RunIdentityError("input changed during identity collection")
                    rows.append([path, content.hexdigest(), bool(before.st_mode & 0o111)])
                finally:
                    os.close(child)
            else:
                raise RunIdentityError("source identity requires regular files and directories")

    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        visit(fd, "")
    finally:
        os.close(fd)
    return digest(rows)


def _images(config: TargetConfig, budget: WorkBudget | None = None) -> list[dict[str, str]]:
    """Probe configured local image identity, without pulling or launching."""
    runtime = config.runtime_lab if isinstance(config.runtime_lab, dict) else {}
    service = runtime.get("service", {})
    if (not isinstance(service, dict) or service.get("enabled") is False
            or not service.get("isolation_image")):
        return []
    from ..sandbox.isolation import select_container_engine
    engine, executable, image = select_container_engine(service)
    if executable:
        try:
            result = subprocess.run([executable, "image", "inspect", "--format", "{{.Id}}", image],
                                    capture_output=True, text=True, timeout=_probe_timeout(budget), check=False)
        except subprocess.TimeoutExpired as exc:
            raise RunIdentityError("image identity probe exceeded round budget") from exc
        image_id = result.stdout.strip()
        if result.returncode == 0 and re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            return [{"engine": engine, "reference_digest": digest(image), "image_id": image_id}]
    raise RunIdentityError("configured image identity could not be verified locally")


def _runtime_executables(config: TargetConfig, budget: WorkBudget | None) -> str:
    from ..tools.build import select_java_executables
    declarations = {("", "")}

    def declared(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("java_home") or value.get("java_bin"):
                declarations.add((str(value.get("java_home") or ""), str(value.get("java_bin") or "")))
            for child in value.values():
                declared(child)
        elif isinstance(value, list):
            for child in value:
                declared(child)

    declared(config.candidates)
    rows: list[Any] = []
    for home, java in sorted(declarations):
        binaries = select_java_executables(home, java)[:2]
        for binary in binaries:
            if binary is None or not binary.exists():
                rows.append([digest([home, java]), "unavailable"])
                continue
            resolved = binary.resolve(strict=True)
            fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                before = os.fstat(fd)
                if not stat.S_ISREG(before.st_mode):
                    raise RunIdentityError("selected runtime is not a regular executable")
                if budget is not None:
                    budget.check()
                    budget.consume("scan_files")
                hashed = hashlib.sha256()
                while block := os.read(fd, 1024 * 1024):
                    if budget is not None:
                        budget.check()
                        budget.consume("scan_bytes", len(block))
                    hashed.update(block)
                after = os.fstat(fd)
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise RunIdentityError("selected runtime changed during identity collection")
                rows.append([digest(str(resolved)), hashed.hexdigest(), bool(before.st_mode & 0o111)])
            finally:
                os.close(fd)
    return digest(rows)


@dataclass(frozen=True)
class RunManifest:
    identity: dict[str, Any]

    @property
    def sha256(self) -> str:
        return digest(self.identity)

    def document(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "identity": self.identity,
                "manifest_sha256": self.sha256, "claim_status": "not-a-finding"}

    @classmethod
    def collect(cls, workspace: Path, config: TargetConfig, round_no: int, *,
                tool_root: Path | None = None, budget: WorkBudget | None = None,
                source_root: Path | None = None,
                execution_options: dict[str, Any] | None = None) -> "RunManifest":
        from ..analysis.languages import SOURCE_INVENTORY_POLICY_VERSION
        from ..memory.evidence_store import STORAGE_POLICY
        from ..sandbox.runner import POC_RESOURCE_POLICY_VERSION
        from ..tools.build import S4_EVIDENCE_POLICY_VERSION
        from ..tools.public_scan import NOVELTY_QUERY_POLICY_VERSION
        from ..tools.service_lifecycle import SERVICE_ISOLATION_POLICY_VERSION
        from ..tools.source_revisions import normalize_source_revision_artifacts

        workspace = workspace.resolve(strict=True)
        tool_root = tool_root or Path(__file__).resolve().parents[3]
        tree = runtime_tree_identity(tool_root, budget=budget)
        plugin = json.loads((tool_root / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        # The caller supplies its actual analysis root. Pipeline uses workspace;
        # autonomous prepared-target layout uses targets/<name>. Never guess.
        source_root = (source_root or workspace).resolve(strict=True)
        if not source_root.is_relative_to(workspace):
            raise RunIdentityError("target root escapes workspace")
        bases, names, invalid = resolve_source_dirs(source_root, config.source_dirs)
        if invalid:
            raise RunIdentityError("configured source identity is incomplete")
        snapshots = []
        for base in bases:
            exclusions = (WORKSPACE_OUTPUTS if not config.source_dirs and base == workspace
                          else frozenset())
            snapshots.append(_snapshot(base, excluded=exclusions, budget=budget))
        if config.poc_src_dir:
            configured_poc = (workspace / config.poc_src_dir).resolve(strict=True)
            if not configured_poc.is_relative_to(workspace):
                raise RunIdentityError("configured PoC source escapes workspace")
            snapshots.append(_snapshot(configured_poc, budget=budget))
        paths = [row["path"] for row in config.jars + config.deps]
        for arm in normalize_source_revision_artifacts(config)["arms"]:
            paths.extend(arm["paths"])
        paths.extend(path for path in [config.benchmark_feedback_path,
                                      config.replay_cohort_calibration_path] if path)
        inputs = []
        store = EvidenceStore(workspace)
        for value in sorted(set(paths)):
            # EvidenceStore's fd traversal rejects outside paths and symlinks.
            # Binary dependencies are hashed separately from JSON projection.
            parts = store._parts(value)
            with store._directory(parts[:-1], create=False) as parent:
                fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                try:
                    before = os.fstat(fd)
                    if not stat.S_ISREG(before.st_mode):
                        raise RunIdentityError("configured input is not a regular file")
                    if budget is not None:
                        budget.consume("scan_files")
                    hashed = hashlib.sha256()
                    while block := os.read(fd, 1024 * 1024):
                        if budget is not None:
                            budget.check()
                            budget.consume("scan_bytes", len(block))
                        hashed.update(block)
                    after = os.fstat(fd)
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise RunIdentityError("configured input changed during identity collection")
                    inputs.append([digest(value), hashed.hexdigest()])
                finally:
                    os.close(fd)
        runtime = config.runtime_lab if isinstance(config.runtime_lab, dict) else {}
        service = runtime.get("service", {})
        manifest = cls({
            "target": config.name, "round": round_no,
            "workspace_digest": digest(str(workspace)),
            "vulngate_version": str(plugin["version"]),
            "vulngate_commit": _revision(tool_root, budget),
            "plugin_manifest_sha256": hashlib.sha256((tool_root / ".codex-plugin/plugin.json").read_bytes()).hexdigest(),
            "tool_tree_sha256": tree["tree_sha256"],
            "python": platform.python_version(), "os": platform.platform(),
            "sandbox_backend": str(service.get("isolation_backend", "auto")) if isinstance(service, dict) else "auto",
            "sandbox_policy_version": SERVICE_ISOLATION_POLICY_VERSION,
            "resource_policy_version": POC_RESOURCE_POLICY_VERSION,
            "evidence_policy_version": S4_EVIDENCE_POLICY_VERSION,
            "novelty_policy_version": NOVELTY_QUERY_POLICY_VERSION,
            "source_policy_version": SOURCE_INVENTORY_POLICY_VERSION,
            "storage_policy_version": STORAGE_POLICY,
            "config_digest": digest(asdict(config)),
            "execution_options_digest": digest(execution_options or {}),
            "source_revision": {"commit": _revision(source_root, budget),
                                "root_digest": digest(str(source_root)),
                                "scope_digest": digest(names), "tree_sha256": digest(snapshots)},
            "input_digest": digest(inputs), "images": _images(config, budget),
            "runtime_executables_digest": _runtime_executables(config, budget),
        })
        if public_document(manifest.document()) != manifest.document():
            raise RunIdentityError("run identity cannot round-trip through evidence policy")
        return manifest


def bind_round(store: Any, manifest: RunManifest) -> str:
    """Bind before checkpoint loads; never upgrade legacy evidence in place."""
    if (manifest.identity.get("target") != store.target
            or manifest.identity.get("round") != store.round_no
            or manifest.identity.get("workspace_digest") != digest(str(store.workspace))):
        raise RunIdentityError("manifest does not describe the requested workspace/target/round")
    relative = store.base.relative_to(store._input_workspace) / MANIFEST_NAME
    try:
        stored = json.loads(store._store.read_text(relative))
    except FileNotFoundError:
        if any(store.base.glob("stage-S[1-8].json")) or any(
                path.is_dir() and any(path.iterdir()) for path in store.base.glob("S[1-8]")):
            raise RunIdentityError("unbound legacy round: inspect only; use a new round")
        try:
            store._store.write_json_once(relative, manifest.document())
        except FileExistsError:
            pass
        stored = json.loads(store._store.read_text(relative))
    if canonical_bytes(stored) != canonical_bytes(manifest.document()):
        raise RunIdentityError("run identity mismatch: use a new round; --force cannot rebind")
    store.manifest_sha256 = manifest.sha256
    for stage in store.completed_stages():
        if stage in {"S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"}:
            store.load_stage(stage)
    from ..memory.artifact_identity import activate
    activate(store._store.binding)
    return manifest.sha256


def bind_cli_round(args: Any) -> Any:
    """Recompute full inputs, never accept a persisted/caller-supplied SHA."""
    from ..analysis.audit_budget import start_round_budget, round_budget_snapshot, DEFAULT_BUDGET_SECONDS
    from ..memory.state import CheckpointStore
    from .work_budget import WorkBudgetExceeded
    if not getattr(args, "config", None):
        raise RunIdentityError("execution requires --config; legacy artifacts are inspect-only")
    try:
        config = TargetConfig.load(Path(args.config))
        options = json.loads(args.identity_options) if getattr(args, "identity_options", None) else {"driver": "matrix"}
    except (OSError, TypeError, ValueError) as exc:
        raise RunIdentityError("CLI identity configuration/options could not be validated") from exc
    if config.name != args.target:
        raise RunIdentityError("config target differs from requested execution target")
    if not isinstance(options, dict):
        raise RunIdentityError("--identity-options must be a JSON object matching the parent controller")
    if getattr(args, "authorized_staging", False):
        options = {**options, "authorized_staging": True,
                   "staging_hosts": sorted(getattr(args, "staging_host", []) or [])}
    try:
        workspace = Path(args.workspace).resolve(strict=True)
        # An explicitly supplied external manifest is operator input, not a
        # persisted S4 result. Bind the exact bytes consumed, even outside the
        # configured source inventory; receipts must supply this same input.
        matrix_input = None
        matrix_path = None
        if getattr(args, "manifest", None):
            path = Path(args.manifest).absolute()
            try:
                relative = path.relative_to(Path(args.workspace).absolute())
            except ValueError:
                relative = path.relative_to(workspace)
            matrix_path = relative
            from ..memory.artifact_identity import ArtifactIdentity
            if not ArtifactIdentity(workspace, args.target, args.round, "0" * 64).covers(relative):
                matrix_input = EvidenceStore(workspace).read_bytes(relative)
                options = {**options, "matrix_manifest_sha256": hashlib.sha256(matrix_input).hexdigest()}
        record = start_round_budget(workspace, args.target, args.round,
                                config.audit_round_timeout_seconds if config.audit_round_timeout_seconds is not None
                                else DEFAULT_BUDGET_SECONDS)
    except (OSError, TypeError, ValueError) as exc:
        raise RunIdentityError("CLI workspace/deadline/input could not be verified") from exc
    snapshot = round_budget_snapshot(record)
    if snapshot["expired"]:
        raise RunIdentityError("execution identity deadline expired")
    budget = WorkBudget(name="cli-identity", wall_seconds=max(0.001, snapshot["remaining_seconds"]),
                        scan_files=500_000, scan_bytes=4 * 1024 * 1024 * 1024)
    try:
        manifest = RunManifest.collect(workspace, config, args.round, budget=budget,
            source_root=Path(args.source_root) if getattr(args, "source_root", None) else workspace,
            execution_options=options)
        store = CheckpointStore(workspace, args.target, args.round)
        bind_round(store, manifest)
        store.cli_matrix_input = matrix_input
        store.cli_matrix_path = matrix_path
    except WorkBudgetExceeded as exc:
        raise RunIdentityError("execution identity exceeded the existing round budget") from exc
    except RunIdentityError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise RunIdentityError("CLI identity inputs could not be verified") from exc
    return store
