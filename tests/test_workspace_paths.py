import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.paths import default_data_dir


class WorkspacePathsTests(unittest.TestCase):
    def test_shared_machine_paths_and_explicit_override_do_not_create_split_data(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"PAPERPLANE_DATA_DIR": ""}):
            root = Path(directory)
            self.assertEqual(default_data_dir(root), root.resolve() / "data")
            data = root.parent / "shared-paperplane-data"
            (root / ".paperplane-paths.json").write_text(json.dumps({"data_dir": str(data)}), encoding="utf-8")
            self.assertEqual(default_data_dir(root), data.resolve())
            with patch.dict("os.environ", {"PAPERPLANE_DATA_DIR": str(root / "isolated-test")}):
                self.assertEqual(default_data_dir(root), (root / "isolated-test").resolve())
            (root / ".paperplane-paths.json").write_text('{"data_dir":"relative-data"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                default_data_dir(root)
