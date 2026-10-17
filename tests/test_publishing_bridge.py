import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from websockets.sync.client import connect
from studio.service import Studio
from studio.publishing_bridge import PublishingBridge, ORIGIN
from studio.adapters import DeliveryError


class PublishingBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Studio(Path(self.tmp.name))
        self.addCleanup(self.app.close)
        self.bridge = self.app.publishing_bridge = PublishingBridge(self.app, port=0)
        self.post = {'title': '测试', 'body': '正文', 'cover': ''}

    def test_wire_protocol_and_private_token(self):
        import concurrent.futures
        setup = self.bridge.setup()
        self.assertNotIn('token', self.bridge.status())
        with connect(setup['address'], origin=ORIGIN) as ws:
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(self.bridge.accounts)
                request = json.loads(ws.recv(timeout=3))
                self.assertEqual(request['method'], 'listPlatforms')
                self.assertEqual(request['token'], setup['token'])
                ws.send(json.dumps({'id': request['id'], 'result': [
                    {'id': 'csdn', 'isAuthenticated': True, 'username': 'test'}]}))
                self.assertTrue(future.result(3)[0]['authenticated'])
        with self.assertRaises(Exception):
            connect(setup['address'], origin='https://evil.example')

    def response(self, method, params, *args, **kwargs):
        if method == 'listPlatforms': return [{'id': 'csdn', 'isAuthenticated': True}]
        if method == 'uploadImage': return {'url': 'https://img-blog.csdnimg.cn/test.png'}
        return {'results': [{'platform': 'csdn', 'success': True, 'postUrl': 'https://editor.csdn.net/md/?articleId=123'}]}

    def test_images_uploaded_before_draft_and_never_published(self):
        media = '/media/' + 'a'*32 + '.png'
        (self.app.db.data_dir/'media').mkdir(exist_ok=True)
        (self.app.db.data_dir/'media'/Path(media).name).write_bytes(b'image fixture')
        with patch.object(self.bridge, 'request', side_effect=self.response) as request:
            result = self.bridge.send({**self.post, 'body': f'![图]({media})', 'cover': media}, 'csdn')
        self.assertEqual(result['status'], 'draft')
        self.assertEqual([c.args[0] for c in request.call_args_list], ['listPlatforms','uploadImage','syncArticle'])
        article = request.call_args_list[-1].args[1]['article']
        self.assertNotIn('/media/', article['markdown'])
        self.assertTrue(article['cover'].startswith('https://'))

    def test_failed_image_blocks_article_submission(self):
        with patch.object(self.bridge, 'request', side_effect=self.response) as request:
            with self.assertRaises(DeliveryError) as error:
                self.bridge.send({**self.post, 'cover':'/media/'+'b'*32+'.png'}, 'csdn')
        self.assertFalse(error.exception.uncertain)
        self.assertEqual(request.call_count, 1)

    def test_receipt_must_match_platform_and_have_real_url(self):
        for receipt in ({}, {'results':[{'platform':'juejin','success':True}]},
                        {'results':[{'platform':'csdn','success':True,'postUrl':'https://evil.example'}]}):
            with patch.object(self.bridge,'request', side_effect=[self.response('listPlatforms',{}),receipt]):
                with self.assertRaises(DeliveryError) as error:self.bridge.send(self.post,'csdn')
            self.assertTrue(error.exception.uncertain)

    def test_queue_snapshots_bridge_and_keeps_old_manual_jobs(self):
        post = self.app.save_post({**self.post, 'stage':'ready'})
        old = self.app.enqueue(post['id'], {'version':post['version'],'channels':['juejin'],'ack_warnings':True})['ids'][0]
        self.app.config.update({'PUBLISH_BRIDGE_ENABLED': True})
        new = self.app.enqueue(post['id'], {'version':post['version'],'channels':['csdn'],'ack_warnings':True})['ids'][0]
        self.app.tick()
        self.assertEqual(self.app.job_detail(old)['status'], 'manual')
        with patch.object(self.bridge, 'send', return_value={'status':'draft','url':'https://editor.csdn.net/md/?articleId=123','note':'已送草稿'}) as send:
            self.app.tick()
        self.assertEqual(send.call_args.args[1], 'csdn')
        self.assertEqual(self.app.job_detail(new)['status'], 'draft')
        self.assertEqual(self.app.job_detail(new)['channel_kind'], 'bridge')


if __name__ == '__main__': unittest.main()
