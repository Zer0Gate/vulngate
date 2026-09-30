"""Challenge-bound S4 sub-agent preflight regression tests."""

import io
import json
import sys
import tempfile
import unittest
from types import SimpleNamespace
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import agent_cli  # noqa: E402


class SpawnProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        (self.workspace / "src").mkdir()
        self.config = self.workspace / "config.json"
        self.config.write_text(json.dumps({"name": "demo", "discovery_date": "2026-10-01",
                                           "source_dirs": ["src"]}))

    def invoke(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = agent_cli.main(["spawn-probe", "--workspace", str(self.workspace),
                                   "--target", "demo", "--round", "1", *args])
        return code, json.loads(output.getvalue())

    def receipt(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = agent_cli.main([
                "parallel-receipt", "--workspace", str(self.workspace),
                "--target", "demo", "--round", "1", "--candidate", "C67", *args,
                "--config", str(self.config),
            ])
        return code, json.loads(output.getvalue())

    def publish_cells(self, payload):
        from agent.memory.artifact_identity import identity_scope
        from agent.orchestrator.run_identity import bind_cli_round
        with identity_scope():
            store = bind_cli_round(SimpleNamespace(workspace=str(self.workspace), target="demo",
                                                  round=1, config=str(self.config)))
            return store.write_artifact("S4", "matrix-runs/C67/cells.json", payload)

    def test_nonce_bound_heartbeat_and_reply_enable_parallelism(self):
        token = "probe_token_123456789"
        code, prepared = self.invoke("--prepare", "--token", token)
        self.assertEqual(0, code)
        heartbeat = Path(prepared["heartbeat_file"])
        heartbeat.write_text("PROBE %s\n" % token, encoding="utf-8")
        code, verified = self.invoke("--status", "ok", "--reply",
                                     "PROBE-DONE %s" % token)
        self.assertEqual(0, code)
        self.assertTrue(verified["verified"])
        self.assertEqual("parallel-per-candidate", verified["decision"])
        artifact = json.loads((self.workspace / "state" / "demo" / "round-01" /
                               "S4" / "spawn-probe.json").read_text())
        self.assertEqual("ok", artifact["status"])
        self.assertTrue(artifact["observed"]["heartbeat_token_valid"])
        self.assertTrue(artifact["observed"]["reply_token_valid"])

    def test_old_or_host_created_heartbeat_cannot_fake_probe_success(self):
        legacy = self.workspace / "state" / "demo" / "round-01" / "S4" / "spawn-probe.heartbeat"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("PROBE host-created\n", encoding="utf-8")
        code, result = self.invoke("--status", "ok", "--reply", "PROBE-DONE")
        self.assertEqual(2, code)
        self.assertFalse(result["verified"])
        self.assertEqual("challenge-missing", result["symptom"])
        self.assertEqual("host-sequential-whole-round", result["decision"])

    def test_generic_reply_downgrades_even_when_heartbeat_has_nonce(self):
        token = "probe_token_987654321"
        self.assertEqual(0, self.invoke("--prepare", "--token", token)[0])
        challenge = json.loads((self.workspace / "state" / "demo" / "round-01" /
                                "S4" / "spawn-probe-challenge.json").read_text())
        Path(challenge["heartbeat_file"]).write_text("PROBE %s\n" % token,
                                                       encoding="utf-8")
        code, result = self.invoke("--status", "ok", "--reply", "ready to help")
        self.assertEqual(2, code)
        self.assertEqual("probe-contract-invalid", result["symptom"])
        self.assertFalse(result["verified"])

    def test_candidate_receipt_requires_fresh_token_and_matrix_artifact(self):
        token = "receipt_token_123456789"
        code, prepared = self.receipt("--prepare", "--token", token)
        self.assertEqual(0, code)
        self.assertEqual("S4/matrix-runs/C67/cells.json",
                         prepared["expected_artifact"])
        cells = (self.workspace / "state" / "demo" / "round-01" /
                 "S4" / "matrix-runs" / "C67" / "cells.json")
        self.publish_cells([{"candidate_id": "C67", "version": "1", "returncode": 0}])
        code, recorded = self.receipt(
            "--status", "completed", "--token", token,
            "--artifact", "S4/matrix-runs/C67/cells.json")
        self.assertEqual(0, code)
        self.assertEqual("completed", recorded["status"])
        code, verified = self.receipt("--verify")
        self.assertEqual(0, code)
        self.assertTrue(verified["verified"])
        self.assertEqual("accept-runtime-artifacts", verified["decision"])

    def test_nonce_cannot_upgrade_unbound_old_cells_or_non_cells(self):
        token = "receipt_token_123456789"
        self.assertEqual(0, self.receipt("--prepare", "--token", token)[0])
        cells = self.workspace / "state/demo/round-01/S4/matrix-runs/C67/cells.json"
        cells.parent.mkdir(parents=True, exist_ok=True)
        cells.write_text('[{"candidate_id":"C67","returncode":0}]')
        self.assertEqual(2, self.receipt("--status", "completed", "--token", token,
                                      "--artifact", "S4/matrix-runs/C67/cells.json")[0])
        self.publish_cells({"status": "executed"})
        self.assertEqual(2, self.receipt("--status", "completed", "--token", token,
                                      "--artifact", "S4/matrix-runs/C67/cells.json")[0])
        self.publish_cells([{"candidate_id": "wrong", "returncode": 0}])
        self.assertEqual(2, self.receipt("--status", "completed", "--token", token,
                                      "--artifact", "S4/matrix-runs/C67/cells.json")[0])
        self.publish_cells([{"candidate_id": "C67", "unrelated": "non-cell"}])
        self.assertEqual(2, self.receipt("--status", "completed", "--token", token,
                                      "--artifact", "S4/matrix-runs/C67/cells.json")[0])

    def test_verified_receipt_rejects_changed_cells_but_inspect_remains_available(self):
        token = "receipt_token_123456789"
        self.assertEqual(0, self.receipt("--prepare", "--token", token)[0])
        cells = self.publish_cells([])
        self.assertEqual(0, self.receipt("--status", "completed", "--token", token,
                                      "--artifact", "S4/matrix-runs/C67/cells.json")[0])
        self.assertEqual(0, self.receipt("--verify")[0])
        cells.write_text('[{"candidate_id":"C67","returncode":0}]')
        self.assertEqual(2, self.receipt("--verify")[0])
        self.assertEqual(0, self.receipt("--inspect")[0])

    def test_receipt_recomputes_source_and_driver_identity(self):
        token = "receipt_token_123456789"
        self.assertEqual(0, self.receipt("--prepare", "--token", token)[0])
        self.assertEqual(2, self.receipt("--verify", "--identity-options", '{"driver":"pipeline"}')[0])
        (self.workspace / "src/changed.py").write_text("changed")
        self.assertEqual(2, self.receipt("--status", "received", "--token", token)[0])
        self.assertEqual(0, self.receipt("--inspect")[0])

    def test_matrix_without_full_configuration_cannot_execute(self):
        from unittest.mock import patch
        output = io.StringIO()
        with patch("agent.cli.runtime.JavaMatrixRunner") as runner, redirect_stdout(output):
            code = agent_cli.main(["matrix", "--workspace", str(self.workspace),
                                   "--target", "demo", "--round", "1"])
        self.assertEqual(2, code)
        self.assertEqual("invalid-run-identity", json.loads(output.getvalue())["status"])
        runner.assert_not_called()

    def test_matrix_default_manifest_requires_exact_current_binding(self):
        from unittest.mock import patch
        from agent.memory.artifact_identity import identity_scope
        from agent.orchestrator.run_identity import bind_cli_round
        def run_matrix():
            with patch("agent.cli.runtime.ShellMatrixRunner") as runner, redirect_stdout(io.StringIO()):
                code = agent_cli.main(["matrix", "--workspace", str(self.workspace),
                    "--target", "demo", "--round", "1", "--lang", "shell", "--config", str(self.config)])
                runner.assert_not_called()
            return code
        with identity_scope():
            store = bind_cli_round(SimpleNamespace(workspace=str(self.workspace), target="demo",
                                                  round=1, config=str(self.config)))
            path = store.artifact_path("S4", "manifest.json")
            path.write_text('{"specs":[{"candidate_id":"C67","script":"echo ok"}]}')
            self.assertEqual(2, run_matrix())
            store.write_artifact("S4", "manifest.json", {"specs": [{"candidate_id": "C67", "script": "echo ok"}]})
            path.write_text('{"specs":[{"candidate_id":"C67","script":"changed"}]}')
            self.assertEqual(2, run_matrix())

    def test_external_operator_manifest_is_identity_bound_and_receipt_recomputes(self):
        path = self.workspace / "operator-matrix.json"
        path.write_text('{"specs":[]}')
        self.assertEqual(0, self.receipt("--prepare", "--manifest", str(path))[0])
        path.write_text('{"specs":[{}]}')
        self.assertEqual(2, self.receipt("--verify", "--manifest", str(path))[0])

    def test_invalid_identity_options_fail_as_contract_error(self):
        self.assertEqual(2, self.receipt("--prepare", "--identity-options", "not-json")[0])

    def test_candidate_receipt_cannot_claim_completion_without_output(self):
        token = "receipt_token_987654321"
        self.assertEqual(0, self.receipt("--prepare", "--token", token)[0])
        code, result = self.receipt("--status", "completed", "--token", token)
        self.assertEqual(2, code)
        self.assertIn("requires", result["error"])
        code, verified = self.receipt("--verify")
        self.assertEqual(2, code)
        self.assertFalse(verified["verified"])
        self.assertEqual("preserve-partial-and-run-host-sequentially",
                         verified["decision"])


if __name__ == "__main__":
    unittest.main()
