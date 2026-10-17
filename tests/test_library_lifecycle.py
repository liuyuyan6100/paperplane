import base64
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

from library.store import Library
from library.server import Server


class LibraryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.lib = Library(self.root / 'library')
        self.source = self.root / 'notes'
        self.source.mkdir()
        (self.source / 'note.md').write_bytes(b'original')
        (self.source / 'child').mkdir()
        (self.source / 'child' / 'note.md').write_bytes(b'nested')

    def tearDown(self):
        self.temp.cleanup()

    def test_existing_nine_column_database_migrates_without_losing_versions(self):
        legacy = self.root / 'legacy'
        (legacy / 'files').mkdir(parents=True)
        digest = hashlib.sha256(b'old').hexdigest()
        (legacy / 'files' / digest).write_bytes(b'old')
        with closing(sqlite3.connect(legacy / 'library.sqlite3')) as db, db:
            db.executescript('CREATE TABLE items(id TEXT PRIMARY KEY,source TEXT,relative_path TEXT,name TEXT,version INTEGER,sha256 TEXT,size INTEGER,text TEXT,updated_at TEXT,UNIQUE(source,relative_path));CREATE TABLE versions(item_id TEXT,version INTEGER,sha256 TEXT,size INTEGER,created_at TEXT,PRIMARY KEY(item_id,version));')
            db.execute('INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?)', ('a'*32, 'Old', 'n.md', 'n.md', 1, digest, 3, 'old', 'before'))
            db.execute('INSERT INTO versions VALUES (?,?,?,?,?)', ('a'*32, 1, digest, 3, 'before'))
        lib = Library(legacy)
        self.assertEqual(lib.content('a'*32, 1)[1], b'old')
        self.assertEqual(lib.put('n.md', b'new', 'Old')['version'], 2)
        self.assertEqual(Library(legacy).listing()['total'], 1)
        self.assertEqual(lib.content('a'*32, 1)[1], b'old')

    def test_folder_scan_preserves_nested_names_and_targets_one_source(self):
        self.lib.register('Notes', self.source)
        other = self.root / 'other'
        other.mkdir()
        (other / 'other.md').write_bytes(b'other')
        self.lib.register('Other', other)
        self.assertEqual(self.lib.scan('Notes')['updated'], 2)
        items = self.lib.listing()['items']
        self.assertEqual({i['relative_path'] for i in items}, {'note.md', 'child/note.md'})
        self.assertEqual(self.lib.scan()['updated'], 1)

    def test_item_removal_preserves_original_and_stays_removed_during_scan(self):
        self.lib.register('Notes', self.source)
        self.lib.scan()
        item = next(i for i in self.lib.listing()['items'] if i['relative_path'] == 'note.md')
        self.lib.change_item(item['id'])
        self.assertEqual((self.source / 'note.md').read_bytes(), b'original')
        self.assertEqual(self.lib.listing()['total'], 1)
        self.assertEqual(self.lib.listing(trash=True)['total'], 1)
        with self.assertRaises(KeyError):
            self.lib.content(item['id'], 1)
        (self.source / 'note.md').write_bytes(b'new content')
        self.assertEqual(self.lib.scan()['skipped_deleted'], 1)
        self.assertEqual(self.lib.scan()['updated'], 0)
        self.lib.change_item(item['id'], restore=True)
        self.assertEqual(self.lib.content(item['id'])[1], b'original')
        self.assertEqual(self.lib.scan()['updated'], 1)
        self.assertEqual(self.lib.content(item['id'])[1], b'new content')
        self.assertEqual(self.lib.content(item['id'], 1)[1], b'original')

    def test_source_removal_pauses_new_imports_and_restores_only_group_removals(self):
        self.lib.register('Notes', self.source)
        self.lib.scan()
        first, second = self.lib.listing()['items']
        self.lib.change_item(first['id'])
        self.assertEqual(self.lib.change_source('Notes')['changed'], 1)
        self.lib.change_item(second['id'])  # repeated removal keeps group provenance
        (self.source / 'new.md').write_bytes(b'new')
        self.assertEqual(self.lib.scan()['seen'], 0)
        self.assertEqual(self.lib.listing()['total'], 0)
        self.assertEqual(self.lib.listing(trash=True)['total'], 2)
        with self.assertRaises(ValueError):
            self.lib.register('Notes', self.source)
        self.assertEqual(self.lib.change_source('Notes', True)['changed'], 1)
        self.assertEqual(self.lib.listing()['total'], 1)
        self.assertEqual(self.lib.listing(trash=True)['items'][0]['id'], first['id'])
        self.assertEqual(self.lib.scan()['updated'], 1)
        self.assertEqual(self.lib.listing()['total'], 2)
        self.assertTrue((self.source / 'new.md').exists())

    def test_folder_upload_isolated_groups_paths_and_source_spoofing(self):
        a = self.lib.import_group('Notes')['source']
        b = self.lib.import_group('Notes')['source']
        self.assertNotEqual(a, b)
        value = {'name':'note.md', 'source':a, 'relative_path':'Notes/child/note.md'}
        item = self.lib.upload(value, b'content')
        self.lib.upload({**value, 'source':b}, b'content')
        self.lib.upload({**value, 'relative_path':'Notes/note.md'}, b'other')
        self.assertEqual(self.lib.listing()['total'], 3)
        self.assertEqual(len(list((self.lib.root / 'files').iterdir())), 2)
        for bad in ('../note.md', '/note.md', 'C:/note.md', 'Notes/_inbox/note.md', 'Notes\\note.md', 'Notes/child/wrong.md'):
            with self.subTest(path=bad), self.assertRaises(ValueError):
                self.lib.upload({**value, 'relative_path':bad}, b'x')
        with self.assertRaises(ValueError):
            self.lib.upload({**value, 'source':'Registered'}, b'x')
        self.lib.change_source(a)
        with self.assertRaises(ValueError):
            self.lib.upload(value, b'updated')
        self.lib.change_source(a, True)
        self.assertEqual(self.lib.content(item['id'])[1], b'content')

    def test_rejects_library_root_ancestor_and_reserved_names(self):
        for path in (self.lib.root, self.lib.root / 'files', self.root):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.lib.register('bad', path)
        for path in (None, ''):
            with self.assertRaises(ValueError):
                self.lib.register('bad', path)
        with self.assertRaises(ValueError):
            self.lib.register('新资料', self.source)

    def test_http_folder_and_trash_mutations_share_auth_guards(self):
        server = Server(('127.0.0.1', 0), self.lib)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def post(path, body, token=server.token, origin=None):
            headers = {'Content-Type':'application/json', 'X-Library-Token':token}
            if origin:
                headers['Origin'] = origin
            with opener.open(urllib.request.Request(base+path, data=json.dumps(body).encode(), headers=headers)) as response:
                return json.load(response)

        try:
            mutations = ['/api/folder-import', '/api/sources/register', '/api/sources/archive', '/api/sources/restore', '/api/items/'+'a'*32+'/archive', '/api/items/'+'a'*32+'/restore']
            for path in mutations:
                with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as caught:
                    post(path, {}, token='')
                self.assertEqual(caught.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                post('/api/folder-import', {'name':'Notes'}, origin='https://evil.example')
            self.assertEqual(caught.exception.code, 403)
            report = post('/api/sources/register', {'name':'Notes', 'path':str(self.source)})
            self.assertEqual(report['scan']['updated'], 2)
            group = post('/api/folder-import', {'name':'Notes'})['source']
            item = post('/api/upload', {'source':group, 'name':'n.md', 'relative_path':'Notes/n.md', 'content':base64.b64encode(b'x').decode()})
            post('/api/items/'+item['id']+'/archive', {})
            with opener.open(base+'/api/items?trash=1') as response:
                self.assertEqual(json.load(response)['items'][0]['id'], item['id'])
            with self.assertRaises(urllib.error.HTTPError) as caught:
                opener.open(base+'/api/items/'+item['id']+'/content')
            self.assertEqual(caught.exception.code, 404)
            post('/api/items/'+item['id']+'/restore', {})
            with opener.open(base+'/api/items/'+item['id']+'/content') as response:
                self.assertEqual(response.read(), b'x')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
