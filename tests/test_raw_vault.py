"""Operator opt-in raw S4 vault, expiry, permissions and encryption gates."""
import importlib.util
import io
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

from agent.memory.evidence_store import WITHHELD
from agent.memory.raw_vault import MAX_STORED_BYTES, RawVault
from agent.cli.legacy import main as cli_main
from agent.tools.build import JavaMatrixRunner, ShellMatrixRunner


SECRET = "unlabelled raw fixture value = demo-business-private"


class RawVaultTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_default_off_creates_no_raw_vault_and_plain_is_explicit(self):
        with patch.dict(os.environ, {"VULNGATE_RAW_VAULT": "off"}):
            runner = ShellMatrixRunner(self.root, "demo", 1)
            runner._write_cells("C1", [{"candidate_id": "C1", "stdout": SECRET}])
        self.assertFalse((self.root / "state/demo/round-01/S4/raw-vault").exists())
        ordinary = self.root / "state/demo/round-01/S4/matrix-runs/C1/cells.json"
        self.assertEqual(WITHHELD, json.loads(ordinary.read_text())[0]["stdout"])
        with patch.dict(os.environ, {"VULNGATE_RAW_VAULT": "plain",
                                     "VULNGATE_RAW_VAULT_DAYS": "1"}):
            runner = JavaMatrixRunner(self.root, "demo", 1)
            runner._write_cells("C1", [{"candidate_id": "C1", "stdout": SECRET}])
        vault_dir = self.root / "state/demo/round-01/S4/raw-vault"
        files = list(vault_dir.glob("*.vault"))
        self.assertEqual(1, len(files))
        self.assertIn(SECRET, files[0].read_text())
        self.assertEqual(0o700, stat.S_IMODE(vault_dir.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(files[0].stat().st_mode))
        self.assertNotIn(SECRET, ordinary.read_text())

    def test_expired_record_cannot_be_read_and_is_scoped_for_purge(self):
        now = [1_700_000_000.0]
        vault = RawVault(self.root, "demo", 1, mode="plain", retention_days=1,
                         clock=lambda: now[0])
        name = vault.capture_cells("C1", [{"stdout": SECRET}])
        self.assertEqual(SECRET, vault.read(name)["cells"][0]["stdout"])
        sibling = vault.store.directory("state/another/round-01/S4/raw-vault") / name
        sibling.write_text("unrelated")
        now[0] += 86400
        longer_policy = RawVault(self.root, "demo", 1, mode="plain", retention_days=30,
                                 clock=lambda: now[0])
        with self.assertRaisesRegex(ValueError, "expired"):
            longer_policy.read(name)
        self.assertEqual(1, vault.purge_expired())
        self.assertFalse((vault.store.root / vault.directory / name).exists())
        self.assertEqual("unrelated", sibling.read_text())

    def test_invalid_configuration_fails_before_runner(self):
        for env in ({"VULNGATE_RAW_VAULT": "maybe"},
                    {"VULNGATE_RAW_VAULT": "plain", "VULNGATE_RAW_VAULT_DAYS": "0"},
                    {"VULNGATE_RAW_VAULT": "fernet"},
                    {"VULNGATE_RAW_VAULT": "plain", "VULNGATE_RAW_VAULT_KEY_FILE": "x"}):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                with self.assertRaises((ValueError, RuntimeError)):
                    ShellMatrixRunner(self.root, "demo", 1)
        self.assertFalse((self.root / "state/demo/round-01/S4/raw-vault").exists())

    def test_record_id_and_symlink_are_not_followed(self):
        now = [float(int(time.time()))]
        vault = RawVault(self.root, "demo", 1, mode="plain", clock=lambda: now[0])
        with self.assertRaises(ValueError):
            vault.read("../../elsewhere")
        vault_dir = vault.store.directory(vault.directory)
        outside = self.root / "outside.txt"
        outside.write_text("outside")
        name = "%010d-%010d-%s.vault" % (
            int(now[0]), int(now[0]) + 86400, "a" * 32)
        (vault_dir / name).symlink_to(outside)
        with self.assertRaises(OSError):
            vault.read(name)
        now[0] += 86400 * 8
        with self.assertRaisesRegex(ValueError, "non-regular"):
            vault.purge_expired()
        self.assertEqual("outside", outside.read_text())

    def test_oversized_existing_record_is_rejected_before_read(self):
        now = [1_700_000_000.0]
        vault = RawVault(self.root, "demo", 1, mode="plain", clock=lambda: now[0])
        name = vault.capture_cells("C1", [{"stdout": SECRET}])
        path = vault.store.root / vault.directory / name
        with path.open("r+b") as stream:
            stream.truncate(MAX_STORED_BYTES + 1)
        with self.assertRaisesRegex(ValueError, "bounded size"):
            vault.read(name)

    def test_cli_purges_only_expired_records_in_selected_round(self):
        now = int(time.time())
        vault = RawVault(self.root, "demo", 1, mode="plain", retention_days=1)
        current = vault.capture_cells("C1", [{"stdout": SECRET}])
        expired = "%010d-%010d-%s.vault" % (now - 2 * 86400,
                                             now - 86400, "b" * 32)
        own_dir = vault.store.directory(vault.directory)
        (own_dir / expired).write_text("expired fixture")
        other = vault.store.directory("state/other/round-01/S4/raw-vault") / expired
        other.write_text("other target")
        argv = ["raw-vault", "purge-expired", "--workspace", str(self.root),
                "--target", "demo", "--round", "1"]
        output = io.StringIO()
        with patch.dict(os.environ, {"VULNGATE_RAW_VAULT": "off"}), redirect_stdout(output):
            self.assertEqual(0, cli_main(argv))
        self.assertEqual(1, json.loads(output.getvalue())["removed"])
        self.assertFalse((own_dir / expired).exists())
        self.assertTrue((own_dir / current).exists())
        self.assertEqual("other target", other.read_text())

    def test_purge_does_not_create_vault_or_require_key(self):
        self.assertEqual(0, RawVault.purge_expired_scope(self.root, "demo", 1))
        self.assertFalse((self.root / "state").exists())
        with self.assertRaisesRegex(ValueError, "workspace does not exist"):
            RawVault.purge_expired_scope(self.root / "missing", "demo", 1)

    @unittest.skipUnless(importlib.util.find_spec("cryptography"),
                         "optional cryptography dependency is not installed")
    def test_encrypted_mode_authenticates_and_never_falls_back_to_plain(self):
        from cryptography.fernet import Fernet

        key_path = self.root / "vault.key"
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(Fernet.generate_key())
        vault = RawVault(self.root, "demo", 1, mode="fernet", key_file=key_path)
        name = vault.capture_cells("C1", [{"stdout": SECRET}])
        raw = (vault.store.root / vault.directory / name).read_text()
        self.assertNotIn(SECRET, raw)
        self.assertEqual(SECRET, vault.read(name)["cells"][0]["stdout"])
        (vault.store.root / vault.directory / name).write_text(raw[:-1] + "x")
        with self.assertRaisesRegex(ValueError, "authentication failed"):
            vault.read(name)
        key_path.chmod(0o644)
        with self.assertRaises(PermissionError):
            RawVault(self.root, "demo", 1, mode="fernet", key_file=key_path)
        key_path.chmod(0o600)
        alias = self.root / "vault-key-alias"
        alias.symlink_to(key_path)
        with self.assertRaises(OSError):
            RawVault(self.root, "demo", 1, mode="fernet", key_file=alias)


if __name__ == "__main__":
    unittest.main()
