"""Fault injection, concurrent deployment and immutable-generation tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import install_plugin as installer
from agent.tools.tool_identity import verify_tree


class InstallTransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.source = self.base / "source"
        self.source.mkdir()
        for name in installer.PAYLOAD:
            path = self.source / name
            if "." in name and not name.startswith(".") or name.isupper():
                path.write_text("fixture\n")
            else:
                path.mkdir()
        self.manifest_path = self.source / ".codex-plugin/plugin.json"
        self.version("1.3.1")
        (self.source / "scripts/fixture.py").write_text("print('old')\n")
        self.dest = self.base / "plugins/vulngate"
        self.market = self.base / "market/marketplace.json"
        self.state = self.dest.parent / ".vulngate-install"
        self.source_link = self.market.parent / "plugins/vulngate"
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "scripts"))

    def version(self, value):
        self.manifest_path.write_text(json.dumps({
            "name": "vulngate", "version": value, "description": "fixture",
            "skills": "./skills/", "interface": {"displayName": "fixture"}}))

    def install(self, enable=False):
        return installer.install(self.source, self.dest, self.market, enable=enable)

    def test_same_version_different_content_is_rejected(self):
        original = self.install()
        (self.source / "scripts/fixture.py").write_text("print('changed')\n")
        with self.assertRaisesRegex(ValueError, "different content"):
            self.install()
        self.assertEqual(original["tree_sha256"], verify_tree(self.dest)["tree_sha256"])

    def test_old_generation_is_retained_and_new_tree_omits_deleted_files(self):
        first = self.install()
        self.version("1.3.2")
        (self.source / "scripts/fixture.py").unlink()
        second = self.install()
        self.assertNotEqual(first["tree_sha256"], second["tree_sha256"])
        self.assertTrue((Path(first["generation"]) / "scripts/fixture.py").is_file())
        self.assertFalse((self.dest / "scripts/fixture.py").exists())
        self.assertEqual(self.source_link.resolve(), self.dest.resolve())
        self.assertEqual(0, self.dest.stat().st_mode & 0o222)
        self.assertEqual(second, self.install())

    def test_corrupted_generation_is_not_reused(self):
        self.install()
        code = self.dest / "scripts/fixture.py"
        code.chmod(0o600)
        code.write_text("tampered")
        with self.assertRaisesRegex(ValueError, "integrity mismatch"):
            self.install()

    def test_symlink_payload_is_rejected_before_publication(self):
        (self.source / "scripts/secret").symlink_to(self.base / "outside")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.install()
        self.assertFalse(self.dest.exists())
        self.assertFalse(self.market.exists())

    def test_failed_activation_restores_marketplace_and_all_pointers(self):
        first = self.install()
        previous_market = self.market.read_bytes()
        self.version("1.3.2")
        error = subprocess.CalledProcessError(19, ["fixture-codex"])
        with patch.object(installer, "find_codex", return_value="fixture-codex"):
            with patch.object(installer.subprocess, "run", side_effect=error):
                with self.assertRaises(subprocess.CalledProcessError):
                    self.install(enable=True)
        self.assertEqual(first["tree_sha256"], verify_tree(self.dest)["tree_sha256"])
        self.assertEqual(previous_market, self.market.read_bytes())
        self.assertEqual(first, json.loads((self.state / "receipt.json").read_text()))
        recovery = json.loads((self.state / "last-recovery.json").read_text())
        self.assertEqual("unknown", recovery["activation_status"])

    def test_crash_at_each_publish_boundary_recovers_previous_generation(self):
        first = self.install()
        original_market = self.market.read_bytes()
        self.version("1.3.2")
        code = '''
import os, sys
from pathlib import Path
import install_plugin as m
source, dest, market = map(Path, sys.argv[1:4])
phase = sys.argv[4]
link, write = m.atomic_link, m.atomic_bytes
def crash_link(path, target):
    link(path, target)
    if (phase == 'dest' and path == dest) or (phase == 'link' and path == market.parent / 'plugins/vulngate'):
        os._exit(73)
def crash_write(path, data):
    write(path, data)
    if phase == 'market' and path == market:
        os._exit(73)
m.atomic_link, m.atomic_bytes = crash_link, crash_write
m.install(source, dest, market, enable=False)
'''
        for phase in ("dest", "link", "market"):
            with self.subTest(phase=phase):
                result = subprocess.run([sys.executable, "-c", code, str(self.source),
                                         str(self.dest), str(self.market), phase], env=self.env)
                self.assertEqual(73, result.returncode)
                installer.recover(self.state, self.dest, self.market, self.source_link)
                installer.recover(self.state, self.dest, self.market, self.source_link)
                self.assertEqual(first["tree_sha256"], verify_tree(self.dest)["tree_sha256"])
                self.assertEqual(original_market, self.market.read_bytes())

    def test_concurrent_installers_publish_consistent_tree(self):
        code = '''
from pathlib import Path
import sys, install_plugin
install_plugin.install(*map(Path, sys.argv[1:4]), enable=False)
'''
        args = [sys.executable, "-c", code, str(self.source), str(self.dest), str(self.market)]
        workers = [subprocess.Popen(args, env=self.env, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE) for _ in range(4)]
        results = []
        try:
            for worker in workers:
                out, err = worker.communicate(timeout=30)
                results.append((worker.returncode, out, err))
        finally:
            for worker in workers:
                if worker.poll() is None:
                    worker.kill()
                worker.communicate()
        for status, out, err in results:
            self.assertEqual(0, status, (out, err))
        receipt = json.loads((self.state / "receipt.json").read_text())
        self.assertEqual(receipt["tree_sha256"], verify_tree(self.dest)["tree_sha256"])
        self.assertEqual(1, len(list((self.state / "generations").iterdir())))
        self.assertEqual(self.source_link.resolve(), self.dest.resolve())

    def crash(self, patch_code):
        code = '''
import os, sys, json
from pathlib import Path
import install_plugin as m
source, dest, market = map(Path, sys.argv[1:4])
state = dest.parent / '.vulngate-install'
''' + patch_code + '\nm.install(source, dest, market, enable=False)\n'
        result = subprocess.run([sys.executable, "-c", code, str(self.source),
                                 str(self.dest), str(self.market)], env=self.env, timeout=30)
        self.assertEqual(73, result.returncode)

    def test_crash_after_private_generation_rename_can_resume(self):
        self.crash('''
replace = m.os.replace
def crash_replace(src, dst):
    replace(src, dst)
    if Path(dst).parent == state / 'generations':
        os._exit(73)
m.os.replace = crash_replace
''')
        self.assertFalse(self.dest.exists())
        receipt = self.install()
        self.assertEqual(receipt["tree_sha256"], verify_tree(self.dest)["tree_sha256"])
        self.assertEqual(0, self.dest.stat().st_mode & 0o222)

    def test_crash_after_legacy_move_restores_original_directory(self):
        self.dest.mkdir(parents=True)
        (self.dest / "original").write_text("legacy")
        self.crash('''
replace = m.os.replace
def crash_replace(src, dst):
    replace(src, dst)
    if Path(src) == dest and Path(dst).name.startswith('legacy-'):
        os._exit(73)
m.os.replace = crash_replace
''')
        self.assertFalse(self.dest.exists())
        installer.recover(self.state, self.dest, self.market, self.source_link)
        self.assertEqual("legacy", (self.dest / "original").read_text())
        self.assertFalse(self.dest.is_symlink())

    def test_committed_journal_finishes_receipt_after_crash(self):
        first = self.install()
        self.version("1.3.2")
        self.crash('''
write = m.atomic_bytes
def crash_write(path, data):
    write(path, data)
    if path == state / 'transaction.json' and json.loads(data).get('committed'):
        os._exit(73)
m.atomic_bytes = crash_write
''')
        self.assertEqual(first, json.loads((self.state / "receipt.json").read_text()))
        installer.recover(self.state, self.dest, self.market, self.source_link)
        installer.recover(self.state, self.dest, self.market, self.source_link)
        receipt = json.loads((self.state / "receipt.json").read_text())
        self.assertEqual("1.3.2", receipt["version"])
        self.assertEqual(receipt["tree_sha256"], verify_tree(self.dest)["tree_sha256"])
        self.assertFalse((self.state / "transaction.json").exists())

    def test_rollback_preserves_concurrent_marketplace_edit(self):
        self.install()
        self.version("1.3.2")
        self.crash('''
write = m.atomic_bytes
def crash_write(path, data):
    write(path, data)
    if path == market:
        os._exit(73)
m.atomic_bytes = crash_write
''')
        changed = json.dumps({"name": "operator-edit", "plugins": []})
        self.market.write_text(changed)
        with self.assertRaisesRegex(RuntimeError, "concurrent change"):
            installer.recover(self.state, self.dest, self.market, self.source_link)
        self.assertEqual(changed, self.market.read_text())
        self.assertTrue((self.state / "transaction.json").exists())

    def test_disk_write_failure_rolls_back_current_pointer(self):
        first = self.install()
        self.version("1.3.2")
        write = installer.atomic_bytes

        def fail_market(path, data):
            if path == self.market:
                raise OSError("simulated disk failure before replacement")
            return write(path, data)

        with patch.object(installer, "atomic_bytes", side_effect=fail_market):
            with self.assertRaises(OSError):
                self.install()
        self.assertEqual(first["tree_sha256"], verify_tree(self.dest)["tree_sha256"])

    def test_activation_with_wrong_cache_identity_is_rejected(self):
        first = self.install()
        self.version("1.3.2")
        result = subprocess.CompletedProcess([], 0, "Installed plugin root: " + first["generation"])
        with patch.object(installer, "find_codex", return_value="fixture-codex"):
            with patch.object(installer.subprocess, "run", return_value=result):
                with self.assertRaisesRegex(RuntimeError, "did not verify"):
                    self.install(enable=True)
        self.assertEqual(first["tree_sha256"], verify_tree(self.dest)["tree_sha256"])

    def test_state_symlink_does_not_change_external_directory(self):
        self.dest.parent.mkdir()
        external = self.base / "external"
        external.mkdir(mode=0o755)
        mode = external.stat().st_mode
        self.state.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "state must not be a symlink"):
            self.install()
        self.assertEqual(mode, external.stat().st_mode)
        self.assertEqual([], list(external.iterdir()))

    def test_lock_timeout_does_not_steal_existing_lock(self):
        lock = self.base / "install.lock"
        with installer.locked(lock):
            with self.assertRaises(TimeoutError):
                with installer.locked(lock, timeout=0):
                    self.fail("second installer acquired an owned lock")


if __name__ == "__main__":
    unittest.main()
