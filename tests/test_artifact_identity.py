"""Run-parent/byte acceptance must not automatically upgrade legacy evidence."""
import json
import os
import tempfile
import unittest
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from contextvars import copy_context
from pathlib import Path
from unittest.mock import patch

from agent.memory.artifact_identity import ACTIVE, ArtifactIdentity, activate, identity_scope
from agent.memory.evidence_store import EvidenceStore
from agent.memory.state import CheckpointStore
from agent.memory.ledger import write_round_artifacts
from agent.orchestrator.run_identity import RunIdentityError
from agent.tools.build import (
    MatrixCell, POCSpec, ShellPOCSpec, _s4_spec_identity,
    converge_s4_cells, JavaMatrixRunner,
)


class ArtifactIdentityTests(unittest.TestCase):
    def setUp(self):
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        self.temp = self.contexts.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        self.identity = ArtifactIdentity(self.root, "fixture", 1, "a" * 64)
        self.contexts.enter_context(identity_scope())
        activate(self.identity)
        self.store = CheckpointStore(self.root, "fixture", 1)

    def test_shapes_and_exact_byte_round_trip(self):
        for value in ([], [{"candidate_id": "A1"}], {"A1": {}}, {}, "plain text\n"):
            with self.subTest(value=value):
                path = self.store.write_artifact("S4", "value.json" if not isinstance(value, str) else "value.txt", value)
                self.assertEqual(value, self.store.read_artifact("S4", path.name))
                record = self.identity.record_path(path.relative_to(self.root))
                self.assertEqual(0o600, (self.root / record).stat().st_mode & 0o777)

    def test_tampered_bytes_and_parent_are_rejected(self):
        path = self.store.write_artifact("S4", "value.json", {"status": "pending"})
        original = path.read_bytes()
        path.write_bytes(original.replace(b"pending", b"success"))
        with self.assertRaises(RunIdentityError):
            self.store.read_artifact("S4", "value.json")
        path.write_bytes(original)
        self.store.manifest_sha256 = "b" * 64
        with self.assertRaises(RunIdentityError):
            self.store.read_artifact("S4", "value.json")

    def test_unbound_legacy_and_path_replay_are_not_upgraded(self):
        path = self.store.artifact_path("S4", "legacy.json")
        path.write_text('{"status":"confirmed"}')
        with self.assertRaises(RunIdentityError):
            self.store.read_artifact("S4", "legacy.json")
        first = self.store.write_artifact("S4", "first.json", [])
        second = self.store.artifact_path("S4", "second.json")
        second.write_bytes(first.read_bytes())
        (self.root / self.identity.record_path(second.relative_to(self.root))).write_bytes(
            (self.root / self.identity.record_path(first.relative_to(self.root))).read_bytes())
        with self.assertRaises(RunIdentityError):
            self.store.read_artifact("S4", "second.json")

    def test_growing_file_and_huge_binding_are_rejected_before_read(self):
        path = self.store.write_artifact("S4", "value.json", [])
        path.write_bytes(b" " * 20000)
        with self.assertRaises(RunIdentityError):
            self.store.read_artifact("S4", "value.json")
        record = self.root / self.identity.record_path(path.relative_to(self.root))
        record.write_bytes(b" " * 20000)
        with self.assertRaises(RunIdentityError):
            self.store.read_artifact("S4", "value.json")

    def test_interrupted_publication_cannot_reuse_prior_binding(self):
        self.store.write_artifact("S4", "value.json", {"status": "old"})
        with patch.object(ArtifactIdentity, "record", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.store.write_artifact("S4", "value.json", {"status": "new"})
        with self.assertRaises(RunIdentityError):
            self.store.read_artifact("S4", "value.json")

    def test_create_once_writer_and_missing_reads_obey_binding(self):
        relative = Path("state/fixture/round-01/S4/once.json")
        evidence = EvidenceStore(self.root)
        evidence.write_json_once(relative, [])
        self.assertEqual("[]", evidence.read_text(relative))
        with self.assertRaises(FileExistsError):
            evidence.write_json_once(relative, ["changed"])
        self.assertIsNone(self.store.read_artifact("S5", "missing.json"))
        self.assertFalse((self.root / "state/fixture/round-01/S5").exists())

    def test_symlink_and_fifo_cannot_be_read(self):
        path = self.store.write_artifact("S4", "value.json", [])
        path.unlink()
        path.symlink_to(self.root / "outside")
        with self.assertRaises(RunIdentityError):
            self.store.read_artifact("S4", "value.json")
        path.unlink()
        os.mkfifo(path)
        with self.assertRaises(RunIdentityError):
            self.store.read_artifact("S4", "value.json")

    def test_threads_inherit_identity_only_with_individual_context_copies(self):
        def worker(index):
            store = CheckpointStore(self.root, "fixture", 1)
            self.assertEqual(self.identity.manifest_sha256, store.manifest_sha256)
            return store.write_artifact("S4", str(index) + ".json", [])
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(copy_context().run, worker, i) for i in range(4)]
            for future in futures:
                path = future.result()
                self.assertEqual([], self.store.read_artifact("S4", path.name))

    def test_s4_directory_alias_cannot_change_binding_scope(self):
        historical = self.root / "state/fixture/round-02/S4/matrix-runs/C1"
        historical.mkdir(parents=True)
        (historical / "cells.json").write_text('[{"candidate_id":"C1","returncode":0}]')
        (self.root / "state/fixture/round-01/S4").symlink_to(historical.parent.parent)
        with self.assertRaises(RunIdentityError):
            converge_s4_cells(self.root, "fixture", 1, "C1")

    def test_current_ledger_coverage_checks_binding_without_s8_checkpoint(self):
        from agent.analysis.coverage import refresh_candidate_coverage
        from agent.analysis.inventory import CoverageStore
        ledger = self.root / "ledger/fixture/round-01/ledger.json"
        ledger.parent.mkdir(parents=True)
        ledger.write_text('{"round":1,"rows":[]}')
        coverage = CoverageStore(self.root, "fixture")
        with self.assertRaises(RunIdentityError):
            refresh_candidate_coverage(coverage, self.root, "fixture", 1)
        EvidenceStore(self.root).write_json(ledger.relative_to(self.root), {"round": 1, "rows": []})
        refresh_candidate_coverage(coverage, self.root, "fixture", 1)
        ledger.write_text('{"round":1,"rows":[{"candidate_id":"forged"}]}')
        with self.assertRaises(RunIdentityError):
            refresh_candidate_coverage(coverage, self.root, "fixture", 1)

    def test_scope_reset_and_history_inspection_do_not_leak_identity(self):
        path = self.store.artifact_path("S4", "legacy.json")
        path.write_text("[]")
        with identity_scope():
            self.assertIsNone(ACTIVE.get())
            plain = CheckpointStore(self.root, "fixture", 1)
            self.assertIsNone(plain.manifest_sha256)
            self.assertEqual([], plain.read_artifact("S4", "legacy.json"))
        self.assertEqual(self.identity, ACTIVE.get())
        self.assertIsNone(CheckpointStore(self.root, "other", 2).manifest_sha256)

    def test_convergence_rejects_unbound_fallback_and_preserves_live_diagnostics(self):
        cells = [{"candidate_id": "A1", "version": "1", "compile_error": "private details"}]
        JavaMatrixRunner(self.root, "fixture", 1)._write_cells("A1", cells)
        merged, sources = converge_s4_cells(self.root, "fixture", 1, "A1", cells)
        self.assertEqual(cells, merged)
        self.assertIn("persisted", sources["sources"])
        fallback = self.store.artifact_path("S4", "sequential-fallback.json")
        fallback.write_text(json.dumps(cells))
        with self.assertRaises(RunIdentityError):
            converge_s4_cells(self.root, "fixture", 1, "A1", cells)

    def test_bound_fallback_can_be_consumed_and_wrong_candidate_is_not_mixed(self):
        cells = [{"candidate_id": "A1", "version": "1"}]
        self.store.write_artifact("S4", "host-fallback.json", {"results": {"A1": cells}})
        self.assertEqual(cells, converge_s4_cells(self.root, "fixture", 1, "A1")[0])
        self.assertEqual([], converge_s4_cells(self.root, "fixture", 1, "A2")[0])

    def test_convergence_replaces_superseded_spec_attempt_but_keeps_sibling(self):
        spec_id = "s4spec-" + "a" * 64
        sibling_spec_id = "s4spec-" + "b" * 64
        old = {
            "candidate_id": "A1", "version": "1", "returncode": 0,
            "s4_spec_id": spec_id, "s4_attempt_id": "00000000-0000-0000-0000-000000000001",
            "s4_attempt_started_ns": 1,
        }
        sibling = {
            "candidate_id": "A1", "version": "1", "returncode": 0,
            "s4_spec_id": sibling_spec_id,
            "s4_attempt_id": "00000000-0000-0000-0000-000000000002",
            "s4_attempt_started_ns": 2,
        }
        self.store.write_artifact(
            "S4", "host-fallback.json", {"results": {"A1": [old, sibling]}})
        current = {
            "candidate_id": "A1", "version": "1", "returncode": 0,
            "s4_spec_id": spec_id, "s4_attempt_id": "00000000-0000-0000-0000-000000000003",
            "s4_attempt_started_ns": 3,
        }
        merged, _ = converge_s4_cells(
            self.root, "fixture", 1, "A1", [current])
        self.assertEqual([current, sibling], merged)

    def test_s4_spec_identity_binds_execution_and_observer_configuration(self):
        java = POCSpec(
            candidate_id="A1", class_name="Probe", src="Probe.java", cells=[],
            safe_mode_jvm_prop="safe.mode", module_opts=["--add-exports=x/y=z"],
            module_run_opts=["--add-opens=x/y=z"], jvm_default={"heap": "1g"},
            effect_observers={"file": {"path": "state/result"}},
        )
        self.assertNotEqual(
            _s4_spec_identity(java, "java"),
            _s4_spec_identity(replace(java, module_run_opts=["--add-opens=x/y=q"]), "java"),
        )
        self.assertNotEqual(
            _s4_spec_identity(java, "java"),
            _s4_spec_identity(replace(java, effect_observers={}), "java"),
        )
        self.assertNotEqual(
            _s4_spec_identity(java, "java"),
            _s4_spec_identity(replace(java, cells=[MatrixCell("2", False)]), "java"),
        )

        shell = ShellPOCSpec(
            candidate_id="A1", script="probe.sh", cells=[],
            env={"MODE": "safe"}, urls={"1": "http://127.0.0.1:8080"},
            input_shape="json", effect_observers={"http": {"origin": "local"}},
            https_tls_certfile="fixture.crt", https_tls_keyfile="fixture.key",
        )
        self.assertNotEqual(
            _s4_spec_identity(shell, "shell"),
            _s4_spec_identity(replace(shell, urls={"1": "http://127.0.0.1:8081"}), "shell"),
        )
        self.assertNotEqual(
            _s4_spec_identity(shell, "shell"),
            _s4_spec_identity(replace(shell, effect_observers={}), "shell"),
        )
        self.assertNotEqual(
            _s4_spec_identity(shell, "shell"),
            _s4_spec_identity(replace(shell, cells=[MatrixCell("2", False)]), "shell"),
        )

    def test_report_and_ledger_resume_revalidate_publication_bytes(self):
        report = EvidenceStore(self.root).write_text("reports/fixture/round-01/finding.md", "pending")
        self.store.save_stage("S7", {"finding_docs": ["finding.md"]})
        self.assertIsNotNone(self.store.load_stage("S7"))
        report.write_text("changed")
        with self.assertRaises(RunIdentityError):
            self.store.load_stage("S7")
        directory = write_round_artifacts(self.root, "fixture", 1, [], [], {}, lang="en")
        self.store.save_stage("S8", {"ledger_dir": str(directory.relative_to(self.root))})
        self.assertIsNotNone(self.store.load_stage("S8"))
        next(directory.glob("*.md")).write_text("changed")
        with self.assertRaises(RunIdentityError):
            self.store.load_stage("S8")


if __name__ == "__main__":
    unittest.main()
