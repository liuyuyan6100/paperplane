"""Isolated public-source guard tests; all credentials and identities are synthetic."""
import base64
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "check_public_source.py"
spec = importlib.util.spec_from_file_location("public_source_guard", SCRIPT)
guard = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = guard
spec.loader.exec_module(guard)


def personal_windows_path():
    return "C:" + "/".join(("", "Users", "synthetic-person", "private-notes"))


class PublicSourceSecurityTests(unittest.TestCase):
    def test_personal_paths_and_owner_domain_are_detected_without_values(self):
        values = [personal_windows_path(), personal_windows_path().replace("/", "\\"),
                  "/".join(("", "home", "synthetic-person", "notes")),
                  "/".join(("", "Users", "synthetic-person", "notes")),
                  "https://private." + "aiclawonline" + ".website/"]
        for value in values:
            with self.subTest(value_kind=values.index(value)):
                findings = guard.check_content("example.txt", ("safe\n" + value).encode())
                self.assertTrue(findings)
                self.assertEqual(findings[0].line, 2)
                self.assertNotIn(value, findings[0].render())
                self.assertNotIn("synthetic-person", findings[0].render())

    def test_generic_paths_brands_and_detector_source_are_allowed(self):
        examples = ["Aiclaw visual style; website.aiclawonline.service.id",
                    "/".join(("", "home", "archive", "config")),
                    "/".join(("", "home", "runner", "work")),
                    "C:" + "/".join(("", "Users", "Public", "example"))]
        self.assertEqual(guard.check_content("example.txt", "\n".join(examples).encode()), [])
        self.assertEqual(guard.check_content("tools/check_public_source.py", SCRIPT.read_bytes()), [])
        self.assertEqual(guard.check_content("tests/test_public_source_security.py", Path(__file__).read_bytes()), [])

    def test_runtime_files_are_rejected_but_env_examples_are_allowed(self):
        private = [".env", ".env.local", ".env.production", "updates.env", "nested/private.json",
                   "private.json.backup", "data/article.json", "data-dev/note.txt", "store.sqlite3",
                   "store.db-wal", "store.sqlite-shm", "cookies.json", "storageState.json",
                   "browser/Default/Preferences", ".auth/state.json", ".gitleaksignore"]
        for path in private:
            with self.subTest(path=path):
                self.assertTrue(guard.forbidden_path(path))
        for path in [".env.example", ".env.cloud.example", "nested/.env.local.example",
                     "studio/db.py", "tests/test_profiles.py"]:
            with self.subTest(path=path):
                self.assertFalse(guard.forbidden_path(path))

    def test_renamed_database_is_detected_by_header(self):
        self.assertEqual(guard.check_content("innocent.bin", b"SQLite format 3\0")[0].kind,
                         "sqlite-database-content")

    def test_utf16_path_is_detected(self):
        self.assertTrue(guard.check_content("note.txt", personal_windows_path().encode("utf-16")))

    def test_export_directory_fails_with_redacted_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "note.txt").write_text(personal_windows_path(), encoding="utf-8")
            result = subprocess.run([sys.executable, str(SCRIPT), "--directory", tmp],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('"note.txt":1: personal-windows-home', result.stdout)
            self.assertNotIn("synthetic-person", result.stdout + result.stderr)

    def test_history_finds_deleted_content_and_same_blob_at_private_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def git(*args):
                return subprocess.run(["git", "-C", tmp, *args], check=True, capture_output=True)

            def commit():
                git("add", "-A")
                git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
                    "commit", "--no-gpg-sign", "-qm", "synthetic fixture")

            git("init", "-q")
            (root / "note.txt").write_text(personal_windows_path(), encoding="utf-8")
            (root / "example.txt").write_text("safe", encoding="utf-8")
            commit()
            (root / "note.txt").unlink()
            (root / "example.txt").rename(root / "private.json")
            commit()
            (root / "private.json").rename(root / "example.txt")
            commit()
            result = subprocess.run([sys.executable, str(SCRIPT), "--repo", tmp],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([sys.executable, str(SCRIPT), "--repo", tmp, "--all-history"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("personal-windows-home", result.stdout)
            self.assertIn('"private.json":1: private-runtime-file', result.stdout)
            self.assertNotIn("synthetic-person", result.stdout + result.stderr)

    def test_symlink_cannot_read_outside_the_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "export"
            source.mkdir()
            secret = root / "outside.txt"
            secret.write_text("private fixture", encoding="utf-8")
            try:
                (source / "link.txt").symlink_to(secret)
            except OSError:
                self.skipTest("creating symlinks is unavailable to this user")
            result = subprocess.run([sys.executable, str(SCRIPT), "--directory", str(source)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("private fixture", result.stdout + result.stderr)


@unittest.skipUnless(shutil.which("gitleaks"), "Gitleaks is installed by the security CI job")
class GitleaksConfigurationTests(unittest.TestCase):
    def scan(self, path, value):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value, encoding="utf-8")
            ignore = root / "empty-ignore"
            ignore.touch()
            result = subprocess.run(["gitleaks", "dir", str(root), "--config", str(ROOT / ".gitleaks.toml"),
                                     "--redact", "--ignore-gitleaks-allow", "--gitleaks-ignore-path", str(ignore),
                                     "--max-decode-depth", "3", "--no-banner"], capture_output=True, text=True)
            return result

    def test_two_exact_redaction_fixtures_are_allowed_only_in_original_file(self):
        # Assemble the values so this regression test is not another allowlist exception.
        values = ["My" + "SecretPass123", "sk-" + "1234567890"]
        for value in values:
            content = 'api_key = "' + value + '"\n'
            with self.subTest(fixture=values.index(value)):
                result = self.scan("tests/test_f05_sanitization.py", content)
                self.assertEqual(result.returncode, 0, result.stderr)
                result = self.scan("tests/another_test.py", content)
                self.assertEqual(result.returncode, 1, result.stderr)

    def test_secret_in_allowed_test_file_still_fails_and_comment_cannot_bypass(self):
        value = "ghp_" + "".join(chr(65 + (i * 7) % 26) for i in range(36))
        result = self.scan("tests/test_f05_sanitization.py", 'token = "' + value + '" # gitleaks:allow\n')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn(value, result.stdout + result.stderr)

    def test_encoded_synthetic_secret_is_detected(self):
        value = "ghp_" + "".join(chr(97 + (i * 11) % 26) for i in range(36))
        payload = base64.b64encode(('token="' + value + '"').encode()).decode()
        result = self.scan("config.txt", payload)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn(value, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
