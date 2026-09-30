"""R06 current input identity, immutable binding and safe legacy inspection."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent.memory.evidence_store import EvidenceStore
from agent.memory.state import CheckpointStore
from agent.orchestrator.config import TargetConfig
from agent.orchestrator.run_identity import RunIdentityError, RunManifest, bind_round
from agent.orchestrator.work_budget import WorkBudget, WorkBudgetExceeded
from agent.tools.tool_identity import IDENTITY_FILE, runtime_tree_identity, tree_identity


class RunIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.source = self.workspace / "src"
        self.source.mkdir()
        (self.source / "target.py").write_text("one", encoding="utf-8")
        self.tool = self.root / "tool"
        (self.tool / ".codex-plugin").mkdir(parents=True)
        (self.tool / ".codex-plugin/plugin.json").write_text(
            '{"name":"vulngate","version":"1.3.0"}', encoding="utf-8")
        (self.tool / "scripts").mkdir()
        (self.tool / "scripts/tool.py").write_text("tool", encoding="utf-8")
        self.config = TargetConfig("fixture", "2026-10-01", source_dirs=["src"])
        self.store = CheckpointStore(self.workspace, "fixture", 1)

    def manifest(self, **kwargs):
        return RunManifest.collect(self.workspace, self.config, 1,
                                   tool_root=self.tool, **kwargs)

    def bound(self):
        manifest = self.manifest()
        bind_round(self.store, manifest)
        self.store.save_stage("S4", {"summaries": {}})
        return manifest

    def test_unchanged_resume_and_parent_binding(self):
        original = self.bound()
        resumed = CheckpointStore(self.workspace, "fixture", 1)
        self.assertEqual(original.sha256, bind_round(resumed, self.manifest()))
        self.assertEqual(original.sha256, resumed.load_stage("S4")["manifest_sha256"])
        resumed.write_artifact("S8", "report.json", {"status": "pending"})
        # Arbitrary dicts may be candidate-keyed maps. Preserve their shape;
        # artifact/cell identity adapters are a separate R06 integration step.
        self.assertEqual({"status": "pending"}, resumed.read_artifact("S8", "report.json"))

    def test_source_bytes_changed_same_size_and_mtime_refused(self):
        original = self.bound()
        source = self.source / "target.py"
        before = source.stat()
        source.write_text("two", encoding="utf-8")
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(RunIdentityError, "mismatch"):
            bind_round(self.store, self.manifest())
        self.assertEqual(original.document(), self.store.read_artifact("S0", "run-manifest.json"))

    def test_config_secret_never_persisted_and_changes_refused(self):
        self.config.runtime_lab = {"service": {"env": {"SECRET": "synthetic-private-value"}}}
        self.bound()
        text = (self.store.base / "S0/run-manifest.json").read_text()
        self.assertNotIn("synthetic-private-value", text)
        self.config.runtime_lab["service"]["env"]["SECRET"] = "different"
        with self.assertRaises(RunIdentityError):
            bind_round(self.store, self.manifest())

    def test_tool_drift_even_with_unchanged_plugin_version_refused(self):
        self.bound()
        (self.tool / "scripts/tool.py").write_text("changed", encoding="utf-8")
        with self.assertRaises(RunIdentityError):
            bind_round(self.store, self.manifest())

    def test_legacy_checkpoint_is_inspection_only(self):
        self.store.save_stage("S4", {"summaries": {}})
        self.assertEqual({}, self.store.load_stage("S4")["summaries"])
        with self.assertRaisesRegex(RunIdentityError, "legacy"):
            bind_round(self.store, self.manifest())
        self.assertFalse((self.store.base / "S0/run-manifest.json").exists())

    def test_legacy_artifact_without_checkpoint_also_refused(self):
        self.store.write_artifact("S4", "matrix-runs/candidate/cells.json", [])
        with self.assertRaisesRegex(RunIdentityError, "legacy"):
            bind_round(self.store, self.manifest())

    def test_unbound_checkpoint_inserted_in_bound_round_refused(self):
        manifest = self.bound()
        legacy = CheckpointStore(self.workspace, "fixture", 1)
        legacy.save_stage("S5", {"status": "complete"})
        with self.assertRaisesRegex(RunIdentityError, "checkpoint"):
            bind_round(legacy, manifest)
        # Inspection does not confer reuse eligibility.
        inspection = CheckpointStore(self.workspace, "fixture", 1)
        self.assertEqual("complete", inspection.load_stage("S5")["status"])

    def test_malformed_and_extra_manifest_fields_refused(self):
        original = self.bound()
        for value in [{**original.document(), "unexpected": True}, [], "invalid"]:
            self.store._store.write_json(
                self.store.base.relative_to(self.workspace) / "S0/run-manifest.json", value)
            with self.assertRaises(RunIdentityError):
                bind_round(self.store, self.manifest())

    def test_private_atomic_create_once_concurrent(self):
        evidence = EvidenceStore(self.workspace)
        def publish(index):
            try:
                evidence.write_json_once("concurrent/manifest.json", {"index": index})
                return index
            except FileExistsError:
                return None
        with ThreadPoolExecutor(max_workers=8) as executor:
            winners = [value for value in executor.map(publish, range(8)) if value is not None]
        self.assertEqual(1, len(winners))
        self.assertEqual(winners[0], json.loads(evidence.read_text("concurrent/manifest.json"))["index"])
        self.assertEqual(0o600, (self.workspace / "concurrent/manifest.json").stat().st_mode & 0o777)

    def test_inspection_missing_artifact_does_not_create_stage(self):
        self.assertIsNone(self.store.read_artifact("S8", "missing.json"))
        self.assertFalse((self.store.base / "S8").exists())

    def test_source_symlink_and_fifo_refused(self):
        link = self.source / "link"
        link.symlink_to(self.source / "target.py")
        with self.assertRaises(RunIdentityError):
            self.manifest()
        link.unlink()
        os.mkfifo(link)
        with self.assertRaises(RunIdentityError):
            self.manifest()

    def test_identity_scan_consumes_shared_budget(self):
        with self.assertRaises(WorkBudgetExceeded):
            self.manifest(budget=WorkBudget(scan_bytes=1))

    def test_disabled_service_does_not_require_image_engine(self):
        self.config.runtime_lab = {"service": {"enabled": False,
                                               "isolation_image": "unused:latest"}}
        with patch("agent.sandbox.isolation.shutil.which", return_value=None):
            self.assertEqual([], self.manifest().identity["images"])

    def test_null_optional_runtime_configuration_is_supported(self):
        self.config.runtime_lab = None
        self.assertEqual([], self.manifest().identity["images"])

    def test_compiled_source_cache_bytes_are_not_ignored(self):
        cache = self.source / "__pycache__"
        cache.mkdir()
        compiled = cache / "target.pyc"
        compiled.write_bytes(b"first")
        self.bound()
        compiled.write_bytes(b"other")
        with self.assertRaises(RunIdentityError):
            bind_round(self.store, self.manifest())

    def test_configured_binary_inputs_change_identity(self):
        artifact = self.workspace / "artifact.jar"
        artifact.write_bytes(b"first")
        self.config.jars = [{"path": "artifact.jar", "version": "1"}]
        self.bound()
        artifact.write_bytes(b"other")
        with self.assertRaises(RunIdentityError):
            bind_round(self.store, self.manifest())

    def test_pipeline_root_does_not_guess_prepared_target_layout(self):
        prepared = self.workspace / "targets/fixture/src"
        prepared.mkdir(parents=True)
        (prepared / "other.py").write_text("prepared")
        self.bound()
        (self.source / "target.py").write_text("changed actual pipeline input")
        with self.assertRaises(RunIdentityError):
            bind_round(self.store, self.manifest())

    def test_explicit_autonomous_source_root_is_bound(self):
        prepared = self.workspace / "targets/fixture"
        (prepared / "src").mkdir(parents=True)
        (prepared / "src/target.py").write_text("prepared")
        first = self.manifest(source_root=prepared)
        (self.source / "target.py").write_text("not selected by autonomous")
        self.assertEqual(first.sha256, self.manifest(source_root=prepared).sha256)
        (prepared / "src/target.py").write_text("changed")
        self.assertNotEqual(first.sha256, self.manifest(source_root=prepared).sha256)

    def test_identical_bytes_at_different_source_roots_are_not_same_run(self):
        for name in ["one", "two"]:
            base = self.workspace / name / "src"
            base.mkdir(parents=True)
            (base / "target.py").write_text("same bytes")
        first = self.manifest(source_root=self.workspace / "one")
        second = self.manifest(source_root=self.workspace / "two")
        self.assertEqual(first.identity["source_revision"]["tree_sha256"],
                         second.identity["source_revision"]["tree_sha256"])
        self.assertNotEqual(first.sha256, second.sha256)

    def test_operator_poc_source_drift_is_rejected(self):
        harness = self.workspace / "harness"
        harness.mkdir()
        (harness / "Probe.sh").write_text("first")
        self.config.poc_src_dir = "harness"
        self.bound()
        (harness / "Probe.sh").write_text("other")
        with self.assertRaises(RunIdentityError):
            bind_round(self.store, self.manifest())

    def test_selected_java_executable_drift_is_rejected_without_execution(self):
        home = self.root / "jdk"
        (home / "bin").mkdir(parents=True)
        for name in ["java", "javac"]:
            (home / "bin" / name).write_bytes(b"fake-runtime")
            (home / "bin" / name).chmod(0o755)
        self.config.candidates = [{"candidate_id": "runtime", "java_home": str(home)}]
        self.bound()
        (home / "bin/java").write_bytes(b"other-runtime")
        with self.assertRaises(RunIdentityError):
            bind_round(self.store, self.manifest())

    def test_engine_selection_normalization_and_no_inspect_fallback(self):
        from agent.orchestrator.run_identity import _images
        from agent.sandbox.isolation import select_container_engine
        from subprocess import CompletedProcess
        for requested in [" PODMAN ", "container"]:
            self.config.runtime_lab = {"service": {"isolation_backend": requested,
                                                   "isolation_image": "fixture:latest"}}
            with patch("agent.sandbox.isolation.shutil.which", side_effect=lambda name: "/mock/" + name), \
                    patch("agent.orchestrator.run_identity.subprocess.run") as command:
                command.return_value = CompletedProcess([], 0, "sha256:" + "a" * 64, "")
                identity = _images(self.config)
                self.assertEqual(select_container_engine(self.config.runtime_lab["service"])[0],
                                 identity[0]["engine"])
                if requested == "container":
                    command.return_value = CompletedProcess([], 1, "", "")
                    with self.assertRaises(RunIdentityError):
                        _images(self.config)
                    self.assertEqual("/mock/docker", command.call_args.args[0][0])

    def test_learned_hint_is_derived_state_not_operator_config_drift(self):
        from agent.autonomous.common import AutoCtx
        from agent.autonomous.scheduling import learn_api_hint
        ctx = AutoCtx(self.workspace, self.config, MagicMock(), True, 1, 1)
        ctx.llm.ask_json.return_value = {"api_hint": "derived guidance"}
        self.bound()
        with patch("agent.autonomous.scheduling.surface_block", return_value="source"):
            self.assertEqual("derived guidance", learn_api_hint(ctx, 1))
        self.assertEqual("", self.config.api_hint)
        self.assertEqual("derived guidance", ctx.api_hint)
        bind_round(self.store, self.manifest())

    def test_implicit_workspace_outputs_do_not_change_source_identity(self):
        self.config.source_dirs = []
        original = self.bound()
        self.store.write_artifact("S4", "summary.json", {"status": "pending"})
        self.assertEqual(original.sha256, self.manifest().sha256)

    def test_selected_output_directory_is_not_silently_excluded(self):
        self.config.source_dirs = ["state"]
        first = self.manifest()
        self.store.write_artifact("S4", "summary.json", {"status": "pending"})
        self.assertNotEqual(first.sha256, self.manifest().sha256)

    def test_developer_payload_excludes_unshipped_state(self):
        before = runtime_tree_identity(self.tool)
        (self.tool / "state").mkdir()
        (self.tool / "state/generated.json").write_text("generated")
        self.assertEqual(before, runtime_tree_identity(self.tool))

    def test_git_dirty_content_is_not_hidden_by_head_oid(self):
        import subprocess
        for args in [["init", "-q"], ["add", "target.py"],
                     ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                      "commit", "-qm", "fixture"]]:
            subprocess.run(["git", "-C", str(self.source), *args], check=True,
                           capture_output=True)
        original = self.manifest()
        (self.source / "target.py").write_text("two")
        changed = self.manifest()
        self.assertEqual(original.identity["source_revision"]["commit"],
                         changed.identity["source_revision"]["commit"])
        self.assertNotEqual(original.sha256, changed.sha256)

    def test_manifest_cannot_be_bound_to_another_target(self):
        other = CheckpointStore(self.workspace, "other", 1)
        with self.assertRaisesRegex(RunIdentityError, "requested workspace"):
            bind_round(other, self.manifest())

    def test_registered_schema_describes_every_identity_field(self):
        from agent.orchestrator.schema_registry import SCHEMA_DIR, list_registered_schemas
        schema = json.loads((SCHEMA_DIR / "run-manifest.json").read_text())
        identity = schema["properties"]["identity"]
        self.assertEqual(set(identity["required"]), set(self.manifest().identity))
        self.assertEqual(set(identity["properties"]), set(identity["required"]))
        self.assertIn("run-manifest.json", list_registered_schemas())

    def test_installed_tree_does_not_fallback_when_tampered(self):
        (self.tool / IDENTITY_FILE).write_text(json.dumps(tree_identity(self.tool)))
        (self.tool / "scripts/tool.py").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "integrity mismatch"):
            self.manifest()

    def test_image_retagging_is_part_of_identity(self):
        self.config.runtime_lab = {"service": {"isolation_backend": "docker", "isolation_image": "fixture:latest"}}
        with patch("agent.sandbox.isolation.shutil.which", side_effect=lambda name: "/mock/" + name), \
                patch("agent.orchestrator.run_identity.subprocess.run") as command:
            def result(argv, **kwargs):
                from subprocess import CompletedProcess
                return CompletedProcess(argv, 0, "sha256:" + "a" * 64 if "image" in argv else "", "")
            command.side_effect = result
            first = self.manifest()
            self.assertEqual("sha256:" + "a" * 64, first.identity["images"][0]["image_id"])
            command.side_effect = lambda argv, **kwargs: __import__("subprocess").CompletedProcess(
                argv, 0, "sha256:" + "b" * 64 if "image" in argv else "", "")
            self.assertNotEqual(first.sha256, self.manifest().sha256)


class PipelineIdentityTests(unittest.TestCase):
    def test_pipeline_cli_returns_nonzero_for_unbound_legacy_round(self):
        from agent.orchestrator import pipeline
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "config.json"
            config.write_text('{"name":"fixture","discovery_date":"2026-10-01"}')
            CheckpointStore(root, "fixture", 1).save_stage("S4", {"summaries": {}})
            self.assertEqual(2, pipeline.main([
                "--target", "fixture", "--round", "1", "--config", str(config),
                "--workspace", str(root), "--stage", "S8", "--offline"]))

    def test_autonomous_cli_returns_nonzero_for_identity_refusal(self):
        from agent.autonomous import reporting
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "config.json"
            config.write_text('{"name":"fixture","discovery_date":"2026-10-01"}')
            with patch.object(reporting, "ROOT", root), \
                    patch.object(reporting, "LLMClient") as client, \
                    patch.object(reporting, "run_loop", return_value=[{
                        "status": "invalid-run-identity", "next_candidates": []}]):
                client.return_value.usage.to_dict.return_value = {}
                self.assertEqual(2, reporting.main([
                    "--name", "fixture", "--config", "config.json", "--offline"]))
            self.assertNotIn("api_hint", json.loads(config.read_text()))

    def test_config_drift_and_force_do_not_reuse_stage_only_checkpoints(self):
        from test_resume_and_scan_regressions import PipelineResumeRegressionTests
        from agent.orchestrator import pipeline
        for force in [False, True]:
            with self.subTest(force=force), tempfile.TemporaryDirectory() as td:
                ctx = PipelineResumeRegressionTests().context(Path(td))
                manifest = ctx.store.read_artifact("S0", "run-manifest.json")
                ctx.config.scope_constraints = "changed operator scope"
                with patch.object(pipeline, "run_s8") as stage:
                    pipeline.run_round(ctx, force=force, only="S8")
                self.assertFalse(stage.called)
                self.assertEqual("invalid-run-identity", ctx.store.read_artifact("S0", "run-identity-status.json")["status"])
                self.assertEqual(manifest, ctx.store.read_artifact("S0", "run-manifest.json"))

    def test_autonomous_config_drift_stops_before_scanning(self):
        from agent.autonomous.common import AutoCtx
        from agent.autonomous import reporting
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = TargetConfig("fixture", "2026-10-01")
            ctx = AutoCtx(root, config, None, True, 1, 1)
            options = {"driver": "autonomous", "offline": True, "max_candidates": 1,
                       "fuzz_budget": 0, "fuzz_seed": None, "fuzz_force": False,
                       "fuzz_skip_minimize": False}
            store = CheckpointStore(root, config.name, 1)
            bind_round(store, RunManifest.collect(root, config, 1, execution_options=options))
            store.save_stage("S2", {"candidates": []})
            config.notes = "changed"
            with patch.object(reporting, "scan_s1_source_rules") as scan:
                result = reporting.run_round(ctx, 1)
            self.assertEqual("invalid-run-identity", result["status"])
            self.assertFalse(scan.called)
