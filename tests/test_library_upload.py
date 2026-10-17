import base64
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
from unittest.mock import patch

from library.server import Server as LibraryServer
from library.store import Library
from studio.server import Server
from studio.service import Studio


class LibraryUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = Library(self.root / 'library')
        self.app = Studio(self.root / 'studio')
        self.addCleanup(self.app.close)
        self.library_server = LibraryServer(('127.0.0.1', 0), self.library)
        self.server = Server(('127.0.0.1', 0), self.app)
        for server in (self.library_server, self.server):
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            def close(server=server, worker=worker):
                server.shutdown()
                server.server_close()
                worker.join(3)
            self.addCleanup(close)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.library_base = f'http://127.0.0.1:{self.library_server.server_port}'
        self.patch = patch('studio.shared_library.BASE', self.library_base)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.opener = build_opener(ProxyHandler({}))

    def post(self, path, values, token=None, origin=None):
        headers = {'Content-Type':'application/json','X-Studio-Token':self.server.token if token is None else token}
        if origin:
            headers['Origin'] = origin
        with self.opener.open(Request(self.base + path, data=json.dumps(values).encode(), headers=headers), timeout=30) as response:
            return json.load(response)

    def test_folder_paths_duplicates_shared_visibility_and_existing_posts_preserved(self):
        original = self.app.save_post({'title':'原稿','body':'保留','stage':'draft'})
        group = self.post('/api/library/folder-import', {'name':'Notes'})['source']
        for path in ('Notes/note.md', 'Notes/child/note.md'):
            self.post('/api/library/upload', {'name':'note.md','source':group,'relative_path':path,'content':base64.b64encode(b'same bytes').decode()})
        with self.opener.open(self.library_base + '/api/items') as response:
            listing = json.load(response)
        self.assertEqual({row['relative_path'] for row in listing['items']}, {'Notes/note.md','Notes/child/note.md'})
        self.assertEqual(len(list((self.library.root / 'files').iterdir())), 1)
        self.assertEqual(self.app.post(original['id'])['body'], '保留')
        with self.assertRaises(HTTPError) as caught:
            self.post('/api/library/upload', {'name':'note.md','source':group,'relative_path':'../note.md','content':'eA=='})
        self.assertEqual(caught.exception.code, 400)

    def test_owner_guards_and_cloud_do_not_mutate_the_local_library(self):
        for path in ('/api/library/folder-import','/api/library/upload'):
            with self.assertRaises(HTTPError) as caught:
                self.post(path, {}, token='')
            self.assertEqual(caught.exception.code, 403)
        with self.assertRaises(HTTPError) as caught:
            self.post('/api/library/folder-import', {'name':'Notes'}, origin='https://evil.example')
        self.assertEqual(caught.exception.code, 403)
        self.server.access = SimpleNamespace(host=self.base.split('//')[1], origin=self.base, verify=lambda _:True)
        with self.assertRaises(HTTPError) as caught:
            self.post('/api/library/folder-import', {'name':'Cloud'}, origin=self.base)
        self.assertEqual(caught.exception.code, 503)
        self.assertEqual(self.library.listing()['total'], 0)

    def test_document_larger_than_old_request_limit_uploads_without_widening_other_routes(self):
        content = base64.b64encode(b'%PDF-test\n' + b'x' * (8 * 1024 * 1024)).decode()
        item = self.post('/api/library/upload', {'name':'large.pdf','content':content})
        self.assertGreater(item['size'], 8 * 1024 * 1024)
        with self.assertRaises(HTTPError) as caught:
            with self.opener.open(Request(self.base + '/api/posts', data=b'{}', headers={'Content-Type':'application/json','X-Studio-Token':self.server.token,'Content-Length':str(len(content))})):
                pass
        self.assertEqual(caught.exception.code, 413)
        with self.assertRaises(HTTPError) as caught:
            self.post('/api/library/upload', {'name':'invalid.pdf','content':'not-base64!'})
        self.assertEqual(caught.exception.code, 400)


if __name__ == '__main__':
    unittest.main()
