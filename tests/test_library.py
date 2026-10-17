import base64
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from pathlib import Path

from library.store import Library
from library.server import Server


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.lib = Library(self.root / 'library')

    def tearDown(self):
        self.temp.cleanup()

    def test_scan_preserves_source_versions_and_skips_private(self):
        source = self.root / 'source'
        source.mkdir()
        note = source / 'note.md'
        note.write_text('初始资料', encoding='utf-8')
        (source / '_inbox').mkdir()
        (source / '_inbox' / 'private.md').write_text('会话资料', encoding='utf-8')
        (source / '.env').write_text('secret')
        self.lib.register('Wiki', source)
        self.assertEqual(self.lib.scan()['updated'], 1)
        item = self.lib.listing()['items'][0]
        self.assertEqual(self.lib.scan()['updated'], 0)
        note.write_text('修改后的资料', encoding='utf-8')
        self.assertEqual(self.lib.scan()['updated'], 1)
        self.assertEqual(self.lib.content(item['id'], 1)[1].decode(), '初始资料')
        self.assertEqual(self.lib.content(item['id'], 2)[1].decode(), '修改后的资料')
        note.unlink()
        self.lib.scan()
        self.assertEqual(self.lib.content(item['id'], 2)[1].decode(), '修改后的资料')
        self.assertEqual(self.lib.listing()['total'], 1)

    def test_deduplicates_files_but_preserves_origins_and_literal_search(self):
        self.lib.put('a.md', b'100%_text', 'A')
        self.lib.put('b.md', b'100%_text', 'B')
        self.assertEqual(len(list((self.lib.root/'files').iterdir())), 1)
        self.assertEqual(len(self.lib.listing('%_')['items']), 2)
        self.assertEqual(len(self.lib.listing('no-match')['items']), 0)
        self.assertEqual(len(self.lib.listing(source='A')['items']), 1)

    def test_http_guards_and_version_download(self):
        server = Server(('127.0.0.1', 0), self.lib)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            data = json.dumps({'name':'test.md', 'content':base64.b64encode(b'hello').decode()}).encode()
            req = urllib.request.Request(base+'/api/upload', data=data, headers={'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                opener.open(req)
            self.assertEqual(caught.exception.code, 403)
            req.add_header('X-Library-Token', server.token)
            req.add_header('Origin', 'https://evil.example')
            with self.assertRaises(urllib.error.HTTPError) as caught:
                opener.open(req)
            self.assertEqual(caught.exception.code, 403)
            req.remove_header('Origin')
            with opener.open(req) as response:
                item = json.load(response)
            self.lib.put('test.md', b'new')
            with opener.open(base+'/api/items/'+item['id']+'/content?version=1') as response:
                self.assertEqual(response.read(), b'hello')
            req = urllib.request.Request(base+'/api/items', headers={'Host':'evil.example'})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                opener.open(req)
            self.assertEqual(caught.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_rejects_unsupported_file_and_checks_integrity(self):
        with self.assertRaises(ValueError):
            self.lib.put('secret.env', b'secret')
        item = self.lib.put('note.md', b'test')
        (self.lib.root/'files'/item['sha256']).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            self.lib.content(item['id'])

    def test_paperplane_import_keeps_provenance_and_original_post(self):
        from studio.service import Studio
        from studio.shared_library import import_material
        app = Studio(self.root / 'studio')
        try:
            original = app.save_post({'title': '原稿', 'body': '原稿保持', 'stage': 'draft'})
            item = self.lib.put('note.md', '共享笔记'.encode())
            with patch('studio.shared_library.material', return_value=self.lib.content(item['id'], 1)):
                result = import_material(app, {'id': item['id'], 'version': 1})
            self.assertEqual(result['post']['stage'], 'idea')
            self.assertEqual(result['post']['body'], '共享笔记')
            self.assertIn(item['id'] + ' v1', result['post']['source'])
            self.assertEqual(app.post(original['id'])['body'], '原稿保持')
            image = self.lib.put('image.png', b'\x89PNG\r\n\x1a\nimage')
            with patch('studio.shared_library.material', return_value=self.lib.content(image['id'], 1)):
                result = import_material(app, {'id': image['id'], 'version': 1})
            self.assertEqual(result['kind'], 'image')
            asset = app.db.one('SELECT * FROM assets WHERE id=?', (result['image']['id'],))
            self.assertIn(image['id'], asset['description'])
        finally:
            app.close()


if __name__ == '__main__':
    unittest.main()
