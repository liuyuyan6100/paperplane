import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tools import control_library


class LibraryPathsTests(unittest.TestCase):
    def test_unconfigured_launcher_does_not_create_a_new_empty_library(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(control_library,'ROOT',Path(folder)),patch.dict('os.environ',{'PAPERPLANE_DATA_DIR':''}):
            with self.assertRaises(RuntimeError):control_library.configured_root()
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_launch_uses_the_selected_instance_library_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);data=root/'instance';data.mkdir();library=root/'shared';library.mkdir()
            (data/'shared-library.json').write_text(json.dumps({'root':str(library)}),encoding='utf-8')
            with patch.object(control_library,'ROOT',root),patch.dict('os.environ',{'PAPERPLANE_DATA_DIR':str(data)}):self.assertEqual(control_library.configured_root(),library.resolve())

    def test_relative_library_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);data=root/'data';data.mkdir();(data/'shared-library.json').write_text('{"root":"relative"}',encoding='utf-8')
            with patch.object(control_library,'ROOT',root),patch.dict('os.environ',{'PAPERPLANE_DATA_DIR':''}),self.assertRaises(RuntimeError):control_library.configured_root()
