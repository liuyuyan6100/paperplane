import json
import tempfile
import threading
import urllib.request
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import patch

from library import feishu


class FeishuTests(unittest.TestCase):
    def test_http_api_writes_require_session_and_reject_foreign_origins(self):
        from library.server import Server
        from library.store import Library
        with tempfile.TemporaryDirectory() as directory:
            server = Server(('127.0.0.1', 0), Library(directory))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = 'http://127.0.0.1:' + str(server.server_port)
            try:
                with patch.object(feishu, 'call_api', return_value={'result': 'ok'}) as business:
                    for headers in [{'Content-Type': 'application/json'},
                        {'Content-Type':'application/json','X-Library-Token':server.token,'Origin':'https://evil.test'}]:
                        with self.assertRaises(urllib.error.HTTPError) as denied:
                            opener.open(urllib.request.Request(base+'/api/feishu/call', b'{}', headers))
                        self.assertEqual(denied.exception.code, 403)
                    self.assertEqual(business.call_count, 0)
                    req = urllib.request.Request(base+'/api/feishu/call', b'{}',
                        {'Content-Type':'application/json','X-Library-Token':server.token})
                    with opener.open(req) as response:
                        self.assertEqual(json.load(response), {'result': 'ok'})
                    self.assertEqual(business.call_count, 1)
            finally:
                server.shutdown()
                thread.join()
                server.server_close()

    def test_online_document_does_not_mirror_and_forces_user(self):
        with patch.object(feishu, '_run', return_value=({'ok': True, 'data': {'document': {
            'content': '# 笔记', 'document_id': 'doc123', 'revision_id': 7}}}, 0)) as cli:
            result = feishu.read_document('https://my.feishu.cn/wiki/abc123')
            self.assertEqual(result['revision_id'], 7)
            self.assertEqual(cli.call_args.args[0][-2:], ['--as', 'user'])
            self.assertNotIn('--output', cli.call_args.args[0])
        for url in ['https://my.feishu.cn.evil.test/wiki/abc123', 'http://my.feishu.cn/wiki/abc123',
                    'https://user@my.feishu.cn/wiki/abc123', 'https://127.0.0.1/wiki/abc123']:
            with self.assertRaises(feishu.FeishuError):
                feishu.document_url(url)

    def test_business_api_excludes_shell_urls_credentials_and_uses_stdin(self):
        with patch.object(feishu, '_data', return_value={'ok': 'remote'}) as data:
            feishu.call_api({'method': 'PATCH', 'path': '/open-apis/docx/v1/documents/abc123/blocks/abc123',
                'data': {'content': '$(danger) `danger`'}})
            self.assertIn('--data', data.call_args.args[0])
            self.assertEqual(json.loads(data.call_args.kwargs['body'])['content'], '$(danger) `danger`')
        for path in ['https://evil.test/open-apis/wiki/v2/spaces', '/open-apis/auth/v3/app_access_token/internal',
                     '/open-apis/authen/v1/access_token', '/open-apis/drive/v1/files/../secrets',
                     '/open-apis/wiki/v2/spaces?access_token=secret']:
            with self.assertRaises(feishu.FeishuError):
                feishu.call_api({'path': path})

    def test_login_failure_never_falls_back_to_bot_or_leaks_credentials(self):
        with patch.object(feishu, '_run', return_value=({'ok': False, 'error': {
            'subtype': 'token_expired', 'message': 'access_token=SECRET'}}, 1)) as cli:
            with self.assertRaises(feishu.FeishuError) as exc:
                feishu.search('笔记')
            self.assertEqual(exc.exception.status, 401)
            self.assertNotIn('SECRET', str(exc.exception))
            self.assertEqual(cli.call_count, 1)
            self.assertIn('user', cli.call_args.args[0])

    def test_paperplane_keeps_link_then_resolves_only_for_task_snapshot(self):
        from studio import feishu as bridge
        from studio.workflows import snapshot_post
        with patch.object(bridge, 'request', return_value={'content': '最新正文', 'url': 'https://my.feishu.cn/wiki/abc123',
                 'document_id': 'doc123', 'revision_id': 9}) as get:
            post = {'id': 'p', 'title': '在线笔记', 'body': '', 'source': bridge.PREFIX + 'https://my.feishu.cn/wiki/abc123'}
            value = snapshot_post(post)
            self.assertEqual(post['body'], '')
            self.assertEqual(value['body'], '最新正文')
            self.assertEqual(value['feishu_reference']['revision_id'], 9)
            self.assertEqual(get.call_count, 1)
            self.assertEqual(snapshot_post({'body': '本地原文'})['body'], '本地原文')
            self.assertEqual(get.call_count, 1)


if __name__ == '__main__':
    unittest.main()
