import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from studio.service import Studio, Problem
from studio.server import export_package
from studio.assets import listing

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="


class XiaohongshuTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parent.parent / "test-results"
        root.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.tmp.cleanup)
        self.app = Studio(self.tmp.name)
        self.addCleanup(self.app.close)
        self.photos = [self.app.upload({"data": PNG, "title": f"图片{i}"})["url"] for i in range(3)]

    def post(self):
        return self.app.save_post({"title": "原文章", "body": "原文章正文", "summary": "概要", "stage": "ready", "xhs": {"title": "图文标题", "caption": "短文案 #实践", "photos": self.photos}})

    def test_manual_workflow_order_snapshot_and_asset_references(self):
        p = self.post()
        self.assertTrue(self.app.preflight(p, ["xiaohongshu"])["ok"])
        job_id = self.app.enqueue(p["id"], {"version": p["version"], "channels": ["xiaohongshu"], "ack_warnings": True})["ids"][0]
        self.app.save_post({"version": p["version"], "xhs": {"title": "修改后", "caption": "新文案", "photos": list(reversed(self.photos))}}, p["id"])
        self.app.tick()
        job = self.app.job_detail(job_id)
        self.assertEqual(job["status"], "manual")
        with zipfile.ZipFile(io.BytesIO(export_package(job, self.app.db.data_dir))) as z:
            self.assertEqual(z.read("caption.txt").decode(), "短文案 #实践")
            manifest = json.loads(z.read("manifest.json"))
            self.assertEqual(manifest["photos"], ["images/01-cover.png", "images/02.png", "images/03.png"])
            self.assertEqual(job["snapshot"]["xhs"]["photos"], self.photos)
        self.assertEqual(len(listing(self.app)["assets"][0]["used_by"]), 1)

    def test_empty_and_missing_photos_are_blocked(self):
        p = self.app.save_post({"title": "文章", "body": "正文", "stage": "ready"})
        self.assertFalse(self.app.preflight(p, ["xiaohongshu"])["ok"])
        p = self.post()
        (self.app.db.data_dir / "media" / Path(self.photos[0]).name).unlink()
        self.assertFalse(self.app.preflight(p, ["xiaohongshu"])["ok"])
        with self.assertRaises(Problem):
            export_package({"channel_id": "xiaohongshu", "snapshot": p}, self.app.db.data_dir)

    def test_bad_photo_paths_duplicates_and_limits_rejected(self):
        for photos in (["/media/../private.json"], ["https://example.com/x.png"], [self.photos[0]] * 2, [self.photos[0]] * 19):
            with self.subTest(photos=photos), self.assertRaises(Problem):
                self.app.save_post({"title": "文章", "xhs": {"photos": photos}})

    def test_other_edits_preserve_channel_note_and_restart(self):
        p = self.post()
        updated = self.app.save_post({"version": p["version"], "body": "新正文"}, p["id"])
        self.assertEqual(updated["xhs"], p["xhs"])
        second = Studio(self.tmp.name)
        self.addCleanup(second.close)
        self.assertEqual(second.post(p["id"])["xhs"], p["xhs"])
        self.assertEqual(len([c for c in second.channels() if c["id"] == "xiaohongshu"]), 1)


if __name__ == "__main__":
    unittest.main()
