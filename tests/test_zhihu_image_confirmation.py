import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from studio.zhihu_images import upload
from studio.adapters import DeliveryError


class ZhihuImageConfirmationTests(unittest.TestCase):
    def window(self, results):
        window = Mock()
        window.stopped.wait.return_value = False
        window.call.side_effect = results
        return window

    def test_hash_without_completed_requires_exact_cdn_image_decode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'image.png'
            path.write_bytes(b'fixture')
            window = self.window([{'upload_file': {'state': 1, 'image_id': '123'}},
                                  {'original_hash': 'v2_ready'}, True])
            with patch('studio.zhihu_images.build_opener') as opener:
                self.assertEqual(upload(window, path), 'https://pic4.zhimg.com/v2_ready')
                opener.assert_not_called()
            self.assertEqual(window.call.call_args.args,
                             ('image-verify', {'url': 'https://pic4.zhimg.com/v2_ready', 'diagnostics': True}))

    def test_unavailable_hash_never_becomes_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'image.png'
            path.write_bytes(b'fixture')
            window = self.window([{'upload_file': {'state': 1, 'image_id': '123'}}] +
                                 [{'original_hash': 'v2_pending'}, False] * 10)
            with patch('studio.zhihu_images._verify_cdn', return_value={'ready': False, 'reason':'fixture-unavailable'}):
                with self.assertRaisesRegex(DeliveryError, '正文未提交'):
                    upload(window, path)
            self.assertEqual(window.upload_diagnostics['status'], 'failed')

    def test_explicit_failed_state_cannot_use_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'image.gif'
            path.write_bytes(b'fixture')
            window = self.window([{'upload_file': {'state': 1, 'image_id': '123'}}] +
                                 [{'status': 'failed', 'original_hash': 'v2_rejected'}] * 10)
            with self.assertRaises(DeliveryError):
                upload(window, path)
            self.assertFalse(any(call.args[0] == 'image-verify' for call in window.call.call_args_list))

    def test_missing_hash_can_confirm_uploaded_object_only_after_cdn_decode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'image.png'
            path.write_bytes(b'fixture')
            grant = {'upload_file': {'state': 0, 'image_id': '123', 'object_key': 'v2_uploaded'},
                     'upload_token': {'access_id': 'fixture', 'access_key': 'fixture', 'access_token': 'fixture'}}
            window = self.window([grant] + [{'status': 'processing'}] * 10 + [True])
            with patch('studio.zhihu_images.build_opener'):
                self.assertEqual(upload(window, path), 'https://pic4.zhimg.com/v2_uploaded')
            self.assertEqual(window.call.call_args.args,
                             ('image-verify', {'url': 'https://pic4.zhimg.com/v2_uploaded', 'diagnostics': True}))


    def test_dotted_image_hash_is_not_discarded_and_cached_string_state_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'image.png'
            path.write_bytes(b'fixture')
            window = self.window([{'upload_file': {'state': '1', 'image_id': '123'},
                                   'upload_token': {'access_key': 'DO_NOT_LOG_SECRET'}},
                                  {'status': 'processing', 'original_hash': 'v2_ready.png',
                                   'token': 'DO_NOT_LOG_SECRET'},
                                  {'ready': True, 'reason': 'loaded', 'width': 3, 'height': 2}])
            with patch('studio.zhihu_images.build_opener') as opener:
                self.assertEqual(upload(window, path), 'https://pic4.zhimg.com/v2_ready.png')
                opener.assert_not_called()
            self.assertEqual(window.upload_diagnostics['checks'][0]['path'], '/v2_ready.png')
            self.assertNotIn('DO_NOT_LOG_SECRET', str(window.upload_diagnostics))
            self.assertNotIn('upload_token', str(window.upload_diagnostics))

    def test_browser_block_can_use_cookie_free_native_cdn_decode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'image.png'
            path.write_bytes(b'fixture')
            window = self.window([{'upload_file': {'state': 1, 'image_id': '123'}},
                                  {'status': 'processing', 'original_hash': 'v2_ready.png'},
                                  {'ready': False, 'reason': 'load-error'}])
            with patch('studio.zhihu_images._verify_cdn', return_value={'ready': True, 'reason':'decoded', 'width':3, 'height':2}) as read:
                self.assertEqual(upload(window, path), 'https://pic4.zhimg.com/v2_ready.png')
                read.assert_called_once_with('https://pic4.zhimg.com/v2_ready.png')
            self.assertEqual(window.upload_diagnostics['checks'][0]['browser']['reason'], 'load-error')
            self.assertEqual(window.upload_diagnostics['checks'][0]['native']['reason'], 'decoded')

    def test_native_read_requires_actual_image_and_sends_no_credentials(self):
        import io
        from PIL import Image
        from studio.zhihu_images import _verify_cdn
        content = io.BytesIO()
        Image.new('RGB', (3, 2), 'white').save(content, 'PNG')
        with patch('studio.zhihu_images.build_opener') as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.status = 200
            response.headers = {'Content-Type': 'image/png'}
            response.read.return_value = content.getvalue()
            result = _verify_cdn('https://pic4.zhimg.com/v2_ready.png')
            self.assertTrue(result['ready'])
            self.assertEqual((result['width'], result['height']), (3, 2))
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header('Referer'), 'https://zhuanlan.zhihu.com/')
            self.assertIsNone(request.get_header('Authorization'))
            self.assertIsNone(request.get_header('Cookie'))
            response.read.return_value = b'<html>not an image</html>'
            self.assertFalse(_verify_cdn('https://pic4.zhimg.com/v2_ready.png')['ready'])
            response.headers = {'Content-Type': 'text/html'}
            self.assertFalse(_verify_cdn('https://pic4.zhimg.com/v2_ready.png')['ready'])
            opener.reset_mock()
            for url in ('https://evil.test/x', 'https://pic4.zhimg.com/../x',
                        'https://pic4.zhimg.com/x?token=secret', 'https://pic4.zhimg.com:443/x'):
                self.assertFalse(_verify_cdn(url)['ready'])
            opener.assert_not_called()

    def test_diagnostic_shape_does_not_copy_unrecognized_strings(self):
        from studio.zhihu_images import _status_shape
        shape = _status_shape({'status': 'PRIVATE_BODY_DO_NOT_LOG', 'original_hash': 'invalid/path',
                               'upload_token': {'access_token': 'SECRET'}, 'body':'PRIVATE_BODY_DO_NOT_LOG'})
        self.assertEqual(shape['status_length'], len('PRIVATE_BODY_DO_NOT_LOG'))
        self.assertNotIn('PRIVATE_BODY_DO_NOT_LOG', str(shape))
        self.assertNotIn('SECRET', str(shape))
        self.assertFalse(shape['original_hash_valid'])

    def test_exact_src_normalizes_scheme_and_preserves_size_and_public_query(self):
        from studio.zhihu_images import _cdn_url, _candidates, _status_shape
        supplied = '//picx.zhimg.com/50/v2-fixture_1440w.jpg?source=fixture'
        expected = 'https://picx.zhimg.com/50/v2-fixture_1440w.jpg?source=fixture'
        self.assertEqual(_cdn_url(supplied), expected)
        self.assertEqual(_cdn_url('http:'+supplied), expected)
        self.assertEqual(_candidates({'src':supplied, 'original_hash':'v2-fixture'}, {}, '.png')[0], ('src',expected))
        shape = _status_shape({'src':supplied})['src_shape']
        self.assertEqual(shape['path'],'/50/v2-fixture_1440w.jpg')
        self.assertEqual(shape['query_keys'],['source'])
        self.assertNotIn('?source=fixture',str(shape))
        for url in ('https://picx.zhimg.com/a/../v2-fixture.jpg', '//evil.test/a.jpg',
                    'https://picx.zhimg.com/a.jpg?token=SECRET', 'https://picx.zhimg.com/a.jpg#fragment',
                    'https://picx.zhimg.com/a.jpg?source=x&cookie=SECRET', 'https://picx.zhimg.com/%2e%2e/a.jpg'):
            self.assertIsNone(_cdn_url(url))

    def test_success_uses_decoded_exact_src_before_bare_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'image.png'; path.write_bytes(b'fixture')
            src = 'http://pic4.zhimg.com/50/v2-fixture_1440w.jpg?source=fixture'
            expected = src.replace('http:','https:',1)
            window = self.window([{'upload_file':{'state':1,'image_id':'123'}},
                                  {'status':'success','src':src,'original_hash':'v2-fixture'},
                                  {'ready':True,'reason':'loaded','width':30,'height':20}])
            with patch('studio.zhihu_images.build_opener') as opener:
                self.assertEqual(upload(window,path),expected);opener.assert_not_called()
            self.assertEqual(window.call.call_args.args,('image-verify',{'url':expected,'diagnostics':True}))

    def test_completed_hash_without_decode_does_not_skip_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'image.png';path.write_bytes(b'fixture')
            window=self.window([{'upload_file':{'state':1,'image_id':'123'}}]+
                               [{'status':'completed','original_hash':'v2-fixture'},False]*10)
            with patch('studio.zhihu_images._verify_cdn',return_value={'ready':False,'reason':'fixture'}):
                with self.assertRaisesRegex(DeliveryError,'正文未提交'):upload(window,path)
            self.assertEqual(window.upload_diagnostics['status'],'failed')

    def test_official_legacy_original_src_gets_mime_extension(self):
        from studio.zhihu_images import _candidates
        for extension, expected_suffix in (('.png', '.png'), ('.jpg', '.jpeg'), ('.jpeg', '.jpeg'), ('.gif', '.gif')):
            with self.subTest(extension=extension):
                values = _candidates({'status':'success','original_src':'//pica.zhimg.com/v2-original'}, {}, extension)
                self.assertEqual(values[0], ('original_src', 'https://pica.zhimg.com/v2-original' + expected_suffix))
        self.assertEqual(_candidates({'original_src':'https://picb.zhimg.com/v2-original.png'}, {}, '.png')[0][1],
                         'https://picb.zhimg.com/v2-original.png')

    def test_official_modern_protocol_keeps_exact_path_and_public_flags(self):
        from studio.zhihu_images import _candidates, _cdn_url
        for flag in ('protocol=v2', 'no_change=1'):
            supplied = 'https://pica.zhimg.com/v2-original?' + flag
            self.assertEqual(_candidates({'src': supplied}, {}, '.png')[0], ('src', supplied))
            self.assertEqual(_candidates({'original_src': supplied}, {}, '.png')[0], ('original_src', supplied))
        for url in ('https://pica.zhimg.com/a?protocol=v3', 'https://picb.zhimg.com/a?no_change=0',
                    'https://picx1.zhimg.com/a', 'https://zhihu-pics-upload.zhimg.com/a',
                    'https://picb.zhimg.com/a?protocol=v2&Signature=PRIVATE_SIGNATURE'):
            self.assertIsNone(_cdn_url(url))

    def test_official_src_precedence_matches_protocol(self):
        from studio.zhihu_images import _candidates
        response = {'src':'https://pica.zhimg.com/v2-resized', 'original_src':'https://picb.zhimg.com/v2-original'}
        self.assertEqual(_candidates(response, {}, '.png')[0], ('original_src','https://picb.zhimg.com/v2-original.png'))
        response['src'] += '?protocol=v2'
        self.assertEqual(_candidates(response, {}, '.png')[0], ('src',response['src']))

    def test_unknown_src_diagnostic_reports_host_path_keys_without_values(self):
        from studio.zhihu_images import _status_shape
        supplied = 'https://unsupported.zhimg.com/v2-original?protocol=v2&Signature=PRIVATE_SIGNATURE&Expires=PRIVATE_EXPIRY'
        shape = _status_shape({'status':'success', 'original_src':supplied})['original_src_shape']
        self.assertFalse(shape['allowed_host'])
        self.assertEqual(shape['host'], 'unsupported.zhimg.com')
        self.assertEqual(shape['path'], '/v2-original')
        self.assertEqual(shape['query_keys'], ['Expires', 'Signature', 'protocol'])
        self.assertNotIn('PRIVATE_SIGNATURE', str(shape))
        self.assertNotIn('PRIVATE_EXPIRY', str(shape))
        self.assertNotIn(supplied, str(shape))

    def test_legacy_original_src_requires_decode_and_never_writes_to_platform(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'image.png'; path.write_bytes(b'fixture')
            supplied = '//pica.zhimg.com/v2-original'
            expected = 'https://pica.zhimg.com/v2-original.png'
            window = self.window([{'upload_file':{'state':1,'image_id':'123'}},
                                  {'status':'success','original_src':supplied},
                                  {'ready':True,'reason':'loaded','width':30,'height':20}])
            with patch('studio.zhihu_images.build_opener') as opener:
                self.assertEqual(upload(window,path),expected)
                opener.assert_not_called()
            self.assertEqual([call.args[0] for call in window.call.call_args_list],
                             ['upload-grant', 'image-status', 'image-verify'])
            self.assertEqual(window.call.call_args.args, ('image-verify', {'url':expected,'diagnostics':True}))

    def private_reference(self, expiration=4102444800):
        return ('https://pic-private.zhihu.com/v2-fixture~render:1440w.png?protocol=v2'
                '&auth_key=PRIVATE_IMAGE_SIGNATURE&expiration='+str(expiration)+'&sceneCode=article'
                '&source=fixture&animatedImageAutoPlay=false&animatedImagePlayCount=0'
                '&incremental=1&overTime=0&precoder=1&retryCount=0&sampling=1')

    def test_official_private_reference_is_preserved_with_renderer_path(self):
        from studio.zhihu_images import _cdn_url, _candidates, _status_shape
        url = self.private_reference()
        self.assertEqual(_cdn_url(url), url)
        self.assertEqual(_candidates({'src':url,'original_src':url,'original_hash':'v2-fixture'},
                                     {'object_key':'v2-other'}, '.png'), [('src',url)])
        shape = _status_shape({'src':url})['src_shape']
        self.assertTrue(shape['allowed_host']);self.assertTrue(shape['private']);self.assertFalse(shape['expired'])
        self.assertEqual(shape['path'], '/v2-fixture~render:1440w.png')
        self.assertIn('auth_key', shape['query_keys'])
        self.assertNotIn('PRIVATE_IMAGE_SIGNATURE', str(shape))
        self.assertNotIn('4102444800', str(shape))

    def test_private_host_and_parameters_are_exactly_scoped(self):
        from studio.zhihu_images import _cdn_url, _candidates
        url = self.private_reference()
        for value in (url.replace('pic-private.zhihu.com','pic-private.zhihu.com.evil.test'),
                      url.replace('pic-private.zhihu.com','other.zhihu.com'), url.replace('https:','http:'),
                      url.replace('protocol=v2','protocol=v1'), url+'&cookie=PRIVATE_ACCOUNT_COOKIE',
                      url.replace('v2-fixture~render:1440w.png','%2e%2e/other'),
                      url.replace('v2-fixture~render:1440w.png','v2%5cother'),
                      url+'&auth_key=duplicate',url.replace('expiration=4102444800','expiration=unknown')):
            with self.subTest(shape=value.split('?')[0]):self.assertIsNone(_cdn_url(value))
        self.assertEqual(_candidates({'src':url+'&cookie=PRIVATE','original_hash':'v2-fixture'},
                                    {'object_key':'v2-other'}, '.png'), [])
        self.assertIsNone(_cdn_url(url.replace('pic-private.zhihu.com','pic4.zhimg.com')))

    def test_private_expiration_is_seconds_and_negative_one_means_unlimited(self):
        from studio.zhihu_images import _private_expired, _verify_cdn
        with patch('studio.zhihu_images.time.time', return_value=1000):
            self.assertTrue(_private_expired(self.private_reference(1000)))
            self.assertFalse(_private_expired(self.private_reference(1001)))
            self.assertFalse(_private_expired(self.private_reference(-1)))
            with patch('studio.zhihu_images.build_opener') as opener:
                self.assertEqual(_verify_cdn(self.private_reference(999))['reason'], 'expired-private-reference')
                opener.assert_not_called()

    def test_expired_private_reference_repolls_original_image_without_guessing_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'image.png';path.write_bytes(b'fixture')
            fresh=self.private_reference(2000)
            window=self.window([{'upload_file':{'state':1,'image_id':'123'}},
                                {'status':'success','src':self.private_reference(900),'original_hash':'v2-fixture'},
                                {'status':'success','src':fresh,'original_hash':'v2-fixture'},
                                {'ready':True,'reason':'loaded','width':3,'height':2}])
            with patch('studio.zhihu_images.time.time', return_value=1000), patch('studio.zhihu_images.build_opener') as opener:
                self.assertEqual(upload(window,path),fresh);opener.assert_not_called()
            calls=window.call.call_args_list
            self.assertEqual([call.args[0] for call in calls], ['upload-grant','image-status','image-status','image-verify'])
            self.assertEqual(calls[1].args[1],calls[2].args[1])
            self.assertNotIn('PRIVATE_IMAGE_SIGNATURE',str(window.upload_diagnostics))
            self.assertEqual(window.upload_diagnostics['checks'][0]['browser']['reason'],'expired-private-reference')

    def test_private_decode_failure_never_becomes_upload_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'image.png';path.write_bytes(b'fixture')
            url=self.private_reference()
            window=self.window([{'upload_file':{'state':1,'image_id':'123'}}]+
                               [{'status':'success','src':url,'original_hash':'v2-fixture'},False]*10)
            with patch('studio.zhihu_images._verify_cdn',return_value={'ready':False,'reason':'fixture'}) as decode:
                with self.assertRaisesRegex(DeliveryError,'正文未提交'):upload(window,path)
                decode.assert_called_once_with(url)
            self.assertNotIn('PRIVATE_IMAGE_SIGNATURE',str(window.upload_diagnostics))
            self.assertTrue(all(call.args[1]['url']==url for call in window.call.call_args_list if call.args[0]=='image-verify'))

    def test_private_native_decode_keeps_per_image_reference_without_account_headers(self):
        import io
        from PIL import Image
        from studio.zhihu_images import _verify_cdn
        content=io.BytesIO();Image.new('RGB',(3,2),'white').save(content,'PNG')
        url=self.private_reference()
        with patch('studio.zhihu_images.build_opener') as opener:
            response=opener.return_value.open.return_value.__enter__.return_value
            response.status=200;response.headers={'Content-Type':'image/png'};response.read.return_value=content.getvalue()
            proof=_verify_cdn(url)
            self.assertTrue(proof['ready'])
            request=opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url,url)
            self.assertIsNone(request.get_header('Authorization'));self.assertIsNone(request.get_header('Cookie'))
            self.assertNotIn('PRIVATE_IMAGE_SIGNATURE',str(proof))


if __name__ == '__main__':
    unittest.main()
