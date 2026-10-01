"""Prove that the installed lint gate rejects an undefined-name fixture."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class QualityGateTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("ruff"), "Ruff dev dependency required")
    def test_actual_lint_configuration_rejects_undefined_name(self):
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--stdin-filename",
             "scripts/undefined_name_fixture.py", "-"],
            cwd=ROOT, input="print(vulngate_undefined_fixture)\n",
            capture_output=True, text=True, timeout=30)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("F821", result.stdout)
        self.assertIn("vulngate_undefined_fixture", result.stdout)
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        for line in workflow.splitlines():
            if "--ignore" in line:
                self.assertNotIn("F821", line)


if __name__ == "__main__":
    unittest.main()
