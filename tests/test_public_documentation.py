"""Execute public developer examples and validate shipped documentation resources."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", ROOT / "NOTICE.md", *[
    ROOT / "docs" / name for name in (
        "USER_GUIDE.md", "PROJECT_COMPARISON.md", "DEPLOYMENT.md",
        "DATA_AND_SECURITY.md", "VERIFICATION.md")],
    *list((ROOT / "docs/visual-styles/aiclaw-warm-engineering-v1").glob("*.md")),
    *list((ROOT / "agents").glob("*.md")),
    *list((ROOT / "skills").rglob("*.md")), ROOT / "tests/README.md"]


class PublicDocumentationTests(unittest.TestCase):
    def test_relative_links_exist(self):
        for doc in DOCS:
            self.assertTrue(doc.is_file(), str(doc))
            for target in re.findall(r"\[[^\]\n]*\]\(([^)\s]+)\)", doc.read_text(encoding="utf-8")):
                if ":" in target or target.startswith("#"):
                    continue
                target = unquote(target.split("#", 1)[0])
                if not target:
                    continue
                with self.subTest(document=str(doc.relative_to(ROOT)), target=target):
                    self.assertTrue((doc.parent / target).exists(), "broken relative documentation link")

    def test_developer_examples_execute_without_private_configuration(self):
        for name in ("README.md", "MEMORIES.md"):
            document = ROOT / "agents" / name
            blocks = re.findall(r"```python\n(.*?)\n```", document.read_text(encoding="utf-8"), re.S)
            self.assertTrue(blocks)
            for block in blocks:
                with self.subTest(document=name):
                    result = subprocess.run([sys.executable, "-c", block], cwd=ROOT,
                                            capture_output=True, text=True, encoding="utf-8", timeout=45)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_portable_style_checksums(self):
        root = ROOT / "docs/visual-styles/aiclaw-warm-engineering-v1"
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        for name, checksum in manifest["files"].items():
            with self.subTest(resource=name):
                self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), checksum)
