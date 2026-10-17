import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop import LocalService
from library.runtime import DesktopLibrary
from tools.control_library import health


class DesktopLibraryTests(unittest.TestCase):
    def test_owned_server_closes_and_releases_port(self):
        with tempfile.TemporaryDirectory() as directory:
            service = DesktopLibrary(directory, port=0)
            service.start()
            port = service.server.server_port
            thread = service.thread
            self.assertEqual(health(port)['app'], 'material-library')
            service.stop()
            service.stop()
            self.assertFalse(thread.is_alive())
            self.assertIsNone(health(port))
            replacement = DesktopLibrary(directory, port=port)
            try:
                replacement.start()
                self.assertIsNotNone(replacement.server)
            finally:
                replacement.stop()

    def test_borrowed_service_survives_client_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            owner = DesktopLibrary(directory, port=0)
            owner.start()
            port = owner.server.server_port
            try:
                borrower = DesktopLibrary(directory, port=port)
                borrower.start()
                self.assertIsNone(borrower.server)
                borrower.stop()
                self.assertTrue(health(port)['ok'])
                other = DesktopLibrary(Path(directory) / 'other', port=port)
                with self.assertRaises(RuntimeError):
                    other.start()
                other.stop()
                self.assertTrue(health(port)['ok'])
            finally:
                owner.stop()

    def test_local_service_owns_library_without_spawning_process(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / 'local'
            data.mkdir()
            (data / 'shared-library.json').write_text(json.dumps(
                {'root': str(Path(directory) / 'library')}), encoding='utf-8')
            library = DesktopLibrary(Path(directory) / 'library', port=0)
            local = LocalService(data)
            with patch('library.runtime.DesktopLibrary', return_value=library), \
                    patch('tools.control_library.subprocess.Popen') as spawn:
                try:
                    local.start()
                    port = library.server.server_port
                    self.assertTrue(health(port)['ok'])
                    spawn.assert_not_called()
                finally:
                    local.stop()
                self.assertIsNone(health(port))

    def test_start_failure_cleans_owned_library(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / 'local'
            data.mkdir()
            (data / 'shared-library.json').write_text(json.dumps(
                {'root': str(Path(directory) / 'library')}), encoding='utf-8')
            library = DesktopLibrary(Path(directory) / 'library', port=0)
            local = LocalService(data)
            with patch('library.runtime.DesktopLibrary', return_value=library), \
                    patch('desktop.Server', side_effect=OSError('test bind failure')):
                with self.assertRaises(OSError):
                    local.start()
            self.assertIsNone(library.server)
            self.assertIsNone(library.thread)
            self.assertIsNone(local.lock)
