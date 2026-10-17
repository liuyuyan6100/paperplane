"""Trusted data aliases must work without probing unrelated absolute paths."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from studio import wechat
from studio.adapters import DeliveryError
from tests.test_wechat import PNG


class WeChatDataAliasTests(unittest.TestCase):
    def test_trusted_data_directory_alias_accepts_image(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            real = root / 'real-data'
            (real / 'media').mkdir(parents=True)
            (real / 'media/cover.png').write_bytes(PNG)
            alias = root / 'data-alias'
            if os.name == 'nt':
                result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(alias), str(real)],
                                        capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            else:
                alias.symlink_to(real, target_is_directory=True)
            try:
                resolved, image = wechat._local_image(str(alias / 'media/cover.png'), alias)
                self.assertEqual(resolved, (real / 'media/cover.png').resolve())
                self.assertEqual(image[0], PNG)
            finally:
                if os.name == 'nt':
                    alias.rmdir()
                else:
                    alias.unlink()

    def test_unrelated_absolute_path_rejected_before_resolution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            unrelated = root.parent / 'untrusted' / 'cover.png'
            if os.name == 'nt':
                unrelated = Path(r'\\untrusted.invalid\share\cover.png')
            real_resolve = Path.resolve
            def guarded(path, *args, **kwargs):
                self.assertNotEqual(path, unrelated, 'untrusted path was resolved')
                return real_resolve(path, *args, **kwargs)
            with patch.object(Path, 'resolve', guarded):
                with self.assertRaises(DeliveryError):
                    wechat._local_image(str(unrelated), root)
