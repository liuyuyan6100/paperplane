"""Real UI + local HTTP routes over an isolated, synthetic data directory."""
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.server import Server
from studio.service import Studio
from tests.test_illustration_positions import BODY, image_bytes


class PositionBrowserTests(unittest.TestCase):
    def test_original_upload_preview_reposition_and_adopt_in_browser(self):
        with tempfile.TemporaryDirectory(prefix='paperplane-position-browser-') as directory:
            root = Path(directory)
            app = Studio(root / 'data')
            server = Server(('127.0.0.1', 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                post = app.save_post({'title': '隔离截图配图验证', 'body': BODY, 'stage': 'draft', 'author': 'Aiclaw'})
                plan = app.illustrations.create({'post_id': post['id'], 'post_version': 1})
                image = root / 'original.png'
                image.write_bytes(image_bytes())
                fixture = root / 'fixture.json'
                fixture.write_text(json.dumps({'url': f'http://127.0.0.1:{server.server_port}',
                    'post_id': post['id'], 'plan_id': plan['id'], 'image': str(image), 'output': str(root)}, ensure_ascii=False), encoding='utf-8')
                with patch.object(app, 'generate_draft') as text, patch('studio.image_provider.generate') as generated:
                    result = subprocess.run(['node', str(Path(__file__).with_suffix('.cjs')), str(fixture)],
                        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=75)
                self.assertEqual(result.returncode, 0, result.stdout + '\n' + result.stderr)
                text.assert_not_called(); generated.assert_not_called()
                current = app.post(post['id'])
                self.assertEqual(current['version'], 3)
                self.assertIn('用户填写的原图说明', current['body'])
                self.assertEqual(app.jobs(), [])
                self.assertEqual(app.db.rows('SELECT * FROM model_calls'), [])
                saved = app.illustrations.get(plan['id'])
                original_path = app.db.data_dir / 'media' / Path(saved['candidates'][0]['image_url']).name
                self.assertEqual(original_path.read_bytes(), image_bytes())
            finally:
                server.shutdown(); server.server_close(); app.close()


if __name__ == '__main__':
    unittest.main()
