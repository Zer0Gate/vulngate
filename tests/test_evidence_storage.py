"""Raw-output confidentiality without changing failure or replay semantics."""
import copy
import io
import json
import os
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent.autonomous.common import AutoCtx
from agent.autonomous.reporting import _evidence_lines as autonomous_evidence_lines
from agent.cli import analysis, audit_exec, evidence, legacy, research, runtime
from agent.memory.evidence_store import (
    EvidencePolicyError, EvidenceStore, WITHHELD, public_document, public_json,
)
from agent.memory.ledger import write_round_artifacts
from agent.orchestrator.pipeline import _evidence_lines as pipeline_evidence_lines
from agent.orchestrator.stages.common import _evidence_from_summary
from agent.memory.state import CheckpointStore
from agent.sandbox.effects import FilesystemDiffCollector
from agent.tools.build import (
    JavaMatrixRunner, ShellMatrixRunner, classify_s4_execution, converge_s4_cells,
    parse_poc_claims, summarize_candidate,
)
from agent.tools.conclusion import derive_conclusion


PRIVATE = 'unlabelled customer text / %70assword%3Ddemo / ZmFrZS1zZWNyZXQ='


def cell(**kwargs):
    result = {
        "candidate_id": "C1", "version": "1", "safe_mode": False,
        "precondition": "none", "returncode": 0, "timed_out": False,
        "observations": {}, "stdout": PRIVATE, "stderr": PRIVATE,
        "poc_claims": parse_poc_claims("LEAKED=" + PRIVATE),
    }
    result.update(kwargs)
    return result


class ProjectionTests(unittest.TestCase):
    def test_nested_raw_fields_and_claims_are_withheld_not_pattern_matched(self):
        value = {"cells": [cell()], "summary": summarize_candidate([cell()]),
                 "config": {"password": PRIVATE, "Authorization": PRIVATE}}
        original = copy.deepcopy(value)
        projected = public_document(value)
        self.assertNotIn(PRIVATE, json.dumps(projected))
        self.assertEqual(value, original)
        self.assertEqual(projected, public_document(projected))
        self.assertEqual(WITHHELD, projected["cells"][0]["stdout"])

    def test_legacy_observation_keys_and_values_are_not_a_backdoor(self):
        data = cell(observations={PRIVATE: PRIVATE}, poc_claims={})
        self.assertNotIn(PRIVATE, public_json(data))
        self.assertEqual(classify_s4_execution([data]),
                         classify_s4_execution([public_document(data)]))

    def test_failure_and_conclusion_semantics_round_trip(self):
        variants = [{}, {"compile_error": PRIVATE}, {"harness_error": PRIVATE},
                    {"compile_error": "", "harness_error": ""},
                    {"timed_out": True, "returncode": -1},
                    {"policy_status": "precondition-unavailable", "returncode": None}]
        for variant in variants:
            with self.subTest(variant=list(variant)):
                data = cell(**variant)
                restored = json.loads(public_json(data))
                self.assertEqual(classify_s4_execution([data]), classify_s4_execution([restored]))
                self.assertEqual(derive_conclusion({}, cells=[data]),
                                 derive_conclusion({}, cells=[restored]))

    def test_sensitive_bound_identity_is_rejected_not_silently_rewritten(self):
        with self.assertRaises(EvidencePolicyError):
            public_document(cell(args=["--password=demo-private-value"]))

    def test_command_and_observer_exception_details_do_not_cross_boundary(self):
        raw = cell(cmd="java --user-data=" + PRIVATE,
                   observation_provenance={"observer_gaps": [PRIVATE]})
        safe = public_document(raw)
        self.assertNotIn(PRIVATE, json.dumps(safe))
        self.assertEqual([WITHHELD], safe["observation_provenance"]["observer_gaps"])
        self.assertEqual(["no-proxied-response-captured"], public_document(
            {"observation_gaps": ["no-proxied-response-captured"]})["observation_gaps"])

    def test_derived_error_and_leak_copies_are_withheld_without_losing_rows(self):
        raw = {"errors": [{"error": PRIVATE, "version": "1"}],
               "leaked": [{"leaked": PRIVATE, "version": "1"}],
               "observed_effects": [{"details": {"error": PRIVATE,
                                                  "changed_paths": [PRIVATE]},
                                     "status": "observed", "value_digest": "a" * 64}],
               "error": "invalid workspace"}
        safe = public_document(raw)
        self.assertEqual(PRIVATE, raw["errors"][0]["error"])
        self.assertEqual(WITHHELD, safe["errors"][0]["error"])
        self.assertEqual(WITHHELD, safe["leaked"][0]["leaked"])
        self.assertEqual({}, safe["observed_effects"][0]["details"])
        self.assertEqual("a" * 64, safe["observed_effects"][0]["value_digest"])
        self.assertEqual(WITHHELD, safe["error"])

    def test_derived_summary_marker_copies_are_withheld(self):
        raw = {"network_side_effects": [PRIVATE], "parsed": [PRIVATE],
               "instantiated": [{"class": PRIVATE, "version": "1"}],
               "gate_blocked": [{"class": PRIVATE, "version": "1"}],
               "effect_evidence": [{"kind": PRIVATE, "detail": PRIVATE,
                                    "version": "1"}],
               "safe_equivalent": [{"kind": PRIVATE, "detail": PRIVATE}],
               "experiment_evidence": [{"declared_sequence": [PRIVATE],
                                        "step_trace": [PRIVATE],
                                        "step_evidence": [PRIVATE],
                                        "state_trace": [PRIVATE],
                                        "warnings": [PRIVATE]}]}
        safe = public_document(raw)
        self.assertNotIn(PRIVATE, json.dumps(safe))
        self.assertEqual([WITHHELD], safe["network_side_effects"])
        self.assertEqual([WITHHELD], safe["parsed"])
        self.assertEqual("1", safe["instantiated"][0]["version"])
        self.assertEqual(safe, public_document(safe))

    def test_matrix_cli_emits_only_typed_execution_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"specs": [{"candidate_id": "C1",
                                                     "script": "echo ok",
                                                     "cells": [{"version": "1"}]}]}))
            args = SimpleNamespace(workspace=directory, target="demo", round=1,
                                   manifest=str(manifest), lang="shell",
                                   authorized_staging=False, staging_host=[])
            config = root / "config.json"
            config.write_text(json.dumps({"name": "demo", "discovery_date": "2026-10-01"}))
            args.config = str(config)
            terminal = io.StringIO()
            with patch.object(runtime, "ShellMatrixRunner") as runner_type:
                runner = runner_type.return_value
                runner.run_manifest.return_value = {
                    "C1": [cell(observations={"LEAKED": PRIVATE})]}
                runner.matrix_dir = root / "state/demo/round-01/S4/matrix-runs"
                runner.execution_budget.snapshot.return_value = {
                    "round_timeout_seconds": 100, "candidate_timeout_seconds": 30,
                    "elapsed_seconds": 2.0, "round_remaining_seconds": 98.0,
                    "round_exhausted": False, "aborted": True,
                    "abort_reason": PRIVATE, "candidates_started": 1,
                    "candidate_timeboxes_exhausted": ["C1"],
                }
                with redirect_stdout(terminal):
                    self.assertEqual(0, runtime.cmd_matrix(args))
            receipt = json.loads(terminal.getvalue())
            self.assertNotIn(PRIVATE, terminal.getvalue())
            self.assertEqual("execution-state-counts-v1", receipt["summary_format"])
            self.assertEqual("executed-no-effect", receipt["candidates"]["C1"]["execution_state"])
            self.assertEqual(1, receipt["candidates"]["C1"]["executed_cell_count"])
            self.assertEqual(1, receipt["s4_execution_budget"][
                "candidate_timeboxes_exhausted_count"])
            self.assertNotIn("abort_reason", receipt["s4_execution_budget"])

    def test_unstructured_exception_and_probe_reply_are_withheld(self):
        payload = {"status": "failed-unhandled-error", "error_type": "RuntimeError",
                   "error": PRIVATE, "runtime_lab": {
                       "schema_version": "runtime-lab-v1", "status": "run-failed",
                       "reason": "RuntimeError: " + PRIVATE},
                   "observed": {"agent_reply": PRIVATE,
                                "reply_token_valid": False}}
        safe = public_document(payload)
        self.assertNotIn(PRIVATE, json.dumps(safe))
        self.assertEqual(WITHHELD, safe["error"])
        self.assertEqual(WITHHELD, safe["runtime_lab"]["reason"])
        self.assertEqual(False, safe["observed"]["reply_token_valid"])

    def test_staging_raw_diagnostics_require_explicit_authorized_flag(self):
        args = SimpleNamespace(authorized_staging=True, show_raw_output=False)
        stream = io.StringIO()
        with redirect_stderr(stream):
            runtime._staging_raw_output(args, PRIVATE, PRIVATE)
        self.assertEqual("", stream.getvalue())
        args.show_raw_output = True
        with redirect_stderr(stream):
            runtime._staging_raw_output(args, PRIVATE, PRIVATE)
        self.assertIn(PRIVATE, stream.getvalue())
        args.authorized_staging = False
        stream = io.StringIO()
        with redirect_stderr(stream):
            runtime._staging_raw_output(args, PRIVATE, PRIVATE)
        self.assertEqual("", stream.getvalue())

    def test_effect_details_are_private_but_observed_kind_and_conclusion_survive(self):
        effect = FilesystemDiffCollector().collect(
            "run-1", "C1", "cell-1", {"status": "ok", "entries": {}},
            {"status": "ok", "entries": {PRIVATE: {"kind": "file", "size": 1,
                                                  "digest": "a" * 64}}}).as_dict()
        raw = cell(cell_id="cell-1", observed_effects=[effect],
                   poc_claims={"fields": {}})
        safe = json.loads(public_json(raw))
        self.assertNotIn(PRIVATE, json.dumps(safe))
        self.assertEqual(effect["value_digest"], safe["observed_effects"][0]["value_digest"])
        self.assertEqual({}, safe["observed_effects"][0]["details"])
        self.assertEqual(classify_s4_execution([raw]), classify_s4_execution([safe]))
        self.assertEqual(derive_conclusion({}, cells=[raw]),
                         derive_conclusion({}, cells=[safe]))

    def test_fixed_observer_gap_preserves_pending_classification(self):
        raw = cell(observation_gaps=["no-proxied-response-captured"])
        self.assertEqual(summarize_candidate([raw])["evidence_gap"],
                         summarize_candidate([public_document(raw)])["evidence_gap"])

    def test_protocol_nonce_is_not_a_credential(self):
        for schema in ("spawn-probe-challenge-v1", "parallel-receipt-challenge-v1",
                       "parallel-receipt-v1"):
            value = {"schema_version": schema, "token": "nonce_123456789"}
            self.assertEqual(value, public_document(value))
        self.assertEqual(WITHHELD, public_document({"token": PRIVATE})["token"])

    def test_unknown_objects_do_not_leak_repr(self):
        with self.assertRaisesRegex(TypeError, "unsupported evidence"):
            public_json({"object": SimpleNamespace(secret=PRIVATE)})

    def test_preformatted_error_lines_and_all_evidence_producers(self):
        summary = summarize_candidate([cell(compile_error=PRIVATE)])
        for lines in (pipeline_evidence_lines(summary),
                      _evidence_from_summary(summary, {"candidate_id": "C1"}),
                      autonomous_evidence_lines({"summary": summary})):
            self.assertNotIn(PRIVATE, "\n".join(lines))
            self.assertIn("COMPILE_ERROR=" + WITHHELD, "\n".join(lines))
        self.assertNotIn(PRIVATE, public_document("COMPILE_ERROR=" + PRIVATE))


class EvidenceStorageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_checkpoint_autonomous_and_embedded_json_writers(self):
        state = CheckpointStore(self.root, "demo", 1)
        ctx = object.__new__(AutoCtx)
        ctx.root, ctx.cfg = self.root, SimpleNamespace(name="demo")
        paths = [state.save_stage("S4", {"cells": [cell()]}),
                 state.write_artifact("S4", "nested/artifact.json", {"cells": [cell()]}),
                 ctx.write_artifact(1, "S4", "auto.json", {"cells": [cell()]}),
                 ctx.write_artifact(1, "S4", "encoded.txt", json.dumps({"cells": [cell()]}))]
        for path in paths:
            self.assertNotIn(PRIVATE, path.read_text())
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            self.assertEqual(0o700, stat.S_IMODE(path.parent.stat().st_mode))
        self.assertEqual(WITHHELD, state.load_stage("S4")["cells"][0]["stderr"])

    def test_matrix_writers_and_convergence_keep_one_live_cell_for_repair(self):
        for runner_type in (JavaMatrixRunner, ShellMatrixRunner):
            with self.subTest(runner=runner_type.__name__):
                runner = runner_type(self.root, "demo", 1)
                raw = cell(compile_error=PRIVATE)
                runner._write_cells("C1", [raw])
                path = runner.matrix_dir / "C1/cells.json"
                self.assertNotIn(PRIVATE, path.read_text())
                combined, _ = converge_s4_cells(self.root, "demo", 1, "C1", [raw])
                self.assertEqual(1, len(combined))
                self.assertEqual(raw, combined[0])
                self.assertEqual(PRIVATE, combined[0]["compile_error"])

    def test_ledger_json_and_markdown_never_stringify_raw_nested_evidence(self):
        out = write_round_artifacts(self.root, "demo", 1,
                                    [{"candidate_id": "C1", "evidence": {"cells": [cell()]}}],
                                    [{"surface": "test", "evidence": {"stderr": PRIVATE}}],
                                    {"metrics": {"stdout": PRIVATE}})
        for path in out.iterdir():
            self.assertNotIn(PRIVATE, path.read_text())
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))

    def test_all_cli_json_emitters_share_projection(self):
        for module in (analysis, audit_exec, evidence, legacy, research, runtime):
            with self.subTest(module=module.__name__):
                stream = io.StringIO()
                with redirect_stdout(stream):
                    module._out({"cells": [cell()], "stderr": PRIVATE})
                self.assertNotIn(PRIVATE, stream.getvalue())
                self.assertEqual(WITHHELD, json.loads(stream.getvalue())["stderr"])

    def test_concurrent_writers_publish_whole_private_documents(self):
        store = EvidenceStore(self.root)
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda n: store.write_json("state/shared.json", {"n": n}), range(30)))
        path = self.root / "state/shared.json"
        self.assertIn(json.loads(path.read_text())["n"], range(30))
        self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
        self.assertEqual([], list(path.parent.glob(".evidence-*.tmp")))

    def test_failed_replace_keeps_prior_content_and_removes_temporary(self):
        store = EvidenceStore(self.root)
        path = store.write_json("state/data.json", {"n": 1})
        with patch("agent.memory.evidence_store.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                store.write_json("state/data.json", {"n": 2})
        self.assertEqual({"n": 1}, json.loads(path.read_text()))
        self.assertEqual([], list(path.parent.glob(".evidence-*.tmp")))

    def test_permission_failure_is_not_ignored(self):
        store = EvidenceStore(self.root)
        with patch("agent.memory.evidence_store.os.fchmod", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                store.write_json("state/data.json", {"n": 2})
        self.assertFalse((self.root / "state/data.json").exists())

    def test_symlink_parent_and_destination_are_rejected(self):
        store = EvidenceStore(self.root)
        actual = self.root / "actual"
        actual.mkdir()
        (self.root / "state").symlink_to(actual, target_is_directory=True)
        with self.assertRaises(OSError):
            store.write_json("state/leak.json", {"stdout": PRIVATE})
        self.assertFalse((actual / "leak.json").exists())
        source = actual / "source.json"
        source.write_text("original")
        (actual / "link.json").symlink_to(source)
        with self.assertRaises(ValueError):
            store.write_json("actual/link.json", {})
        with self.assertRaises(OSError):
            store.read_text("actual/link.json")
        self.assertEqual("original", source.read_text())

    def test_path_escape_rejected(self):
        store = EvidenceStore(self.root)
        for path in ("../escape", "/absolute", "state/../../escape", "state\\escape", ""):
            with self.subTest(path=path), self.assertRaises(ValueError):
                store.write_json(path, {})


if __name__ == "__main__":
    unittest.main()
