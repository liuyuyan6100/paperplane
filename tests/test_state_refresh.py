"""Deterministically delay state responses across navigation and user input."""
from pathlib import Path
import subprocess
import unittest


class StateRefreshTests(unittest.TestCase):
    def test_response_does_not_replace_a_changed_or_editing_page(self):
        script = Path(__file__).with_name("verify_state_refresh.cjs")
        result = subprocess.run(["node", "--test", str(script)], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
