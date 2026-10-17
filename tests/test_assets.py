import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from studio import assets
from studio.migration import backup_bundle
from studio.service import Studio, Problem
from tools.restore_studio import restore

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="


class AssetTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parent.parent / "test-results"
        root.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = Studio(self.root / "data")
        self.addCleanup(self.app.close)

    def upload(self, **values):
        return self.app.upload({"data": PNG, "title": "检查网络", "collection": "网络问题", "step": 2, "description": "记录真实现场", **values})

    def test_reuse_metadata_and_hide_preserve_article_images(self):
        image = self.upload()
        p1 = self.app.save_post({"title": "排查记录", "body": f"![现场]({image['url']})"})
        self.app.save_post({"title": "经验总结", "cover": image["url"]})
        row = assets.listing(self.app)["assets"][0]
        self.assertEqual(len(row["used_by"]), 2)
        assets.update(self.app, image["id"], {"title": "确认网络故障", "collection": "网络排查", "archived": True, "origin": "ai-diagram"})
        row = assets.listing(self.app)["assets"][0]
        self.assertEqual(row["origin"], "upload")
        self.assertTrue(row["archived"])
        self.assertEqual(self.app.post(p1["id"])["body"], p1["body"])
        self.assertTrue((self.app.db.data_dir / "media" / Path(image["url"]).name).exists())
        assets.update(self.app, image["id"], {"archived": False})
        self.assertFalse(assets.listing(self.app)["assets"][0]["archived"])

    def test_validation_precedes_file_creation(self):
        for value in ({"step": -1}, {"step": True}, {"description": []}, {"origin": "fake"}, {"title": ""}):
            with self.subTest(value=value), self.assertRaises(Problem):
                self.upload(**value)
        self.assertEqual(assets.listing(self.app)["assets"], [])
        self.assertFalse((self.app.db.data_dir / "media").exists())

    def test_legacy_media_index_is_idempotent(self):
        folder = self.app.db.data_dir / "media"
        folder.mkdir()
        (folder / ("a" * 32 + ".png")).write_bytes(base64.b64decode(PNG))
        assets.index_existing(self.app)
        assets.index_existing(self.app)
        rows = assets.listing(self.app)["assets"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["origin"], "imported")

    def test_asset_archive_and_original_image_survive_complete_restore(self):
        image = self.upload(origin="ai-diagram")
        archive = self.root / "backup.zip"
        archive.write_bytes(backup_bundle(self.app))
        target = self.root / "restored"
        restore(archive, target)
        restored = Studio(target)
        self.addCleanup(restored.close)
        self.assertEqual(assets.listing(restored)["assets"], assets.listing(self.app)["assets"])
        self.assertEqual((target / "media" / Path(image["url"]).name).read_bytes(), base64.b64decode(PNG))

    def test_diagram_validates_structure_and_does_not_save_content(self):
        post = self.app.save_post({"title": "网络排查", "body": "检查网络，再验证结果"})
        proposal = {"title": "排查流程", "steps": [{"title": "检查网络", "detail": "记录连接情况"}, {"title": "验证", "detail": "结果待验证"}]}
        with patch.object(self.app, "generate_draft", return_value="```json\n" + json.dumps(proposal) + "\n```"):
            self.assertEqual(assets.diagram(self.app, post, "做流程图"), proposal)
        for value in ([], {}, {**proposal, "steps": []}, {**proposal, "steps": [{"title": "x"}, {}]}, {**proposal, "title": "x" * 41}):
            with self.subTest(value=value), patch.object(self.app, "generate_draft", return_value=json.dumps(value)), self.assertRaises(Problem):
                assets.diagram(self.app, post, "画图")
        self.assertEqual(assets.listing(self.app)["assets"], [])
        self.assertEqual(self.app.post(post["id"])["version"], 1)


if __name__ == "__main__":
    unittest.main()
