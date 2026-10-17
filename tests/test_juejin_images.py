import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from studio import juejin_images as images
from studio.adapters import DeliveryError


class JuejinImagesTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/'image.png';self.path.write_bytes(b'123456789')
        self.window=Mock();self.window.call.side_effect=[{'AccessKeyId':'access','SecretAccessKey':'secret','SessionToken':'session'},'https://p3.byteimg.com/test.png']
        self.address={'UploadAddress':{'StoreInfos':[{'StoreUri':'test/path','Auth':'upload'}], 'UploadHosts':['example.volces.com'],'SessionKey':'session/key'}}
        self.commit={'Results':[{'Uri':'test/path','UriStatus':2000}]}

    def test_upload_uses_native_requests_and_browser_only_for_grant_and_url(self):
        def request(url,method,headers,body=None):
            if 'ApplyImageUpload' in url:
                self.assertEqual(method,'GET');self.assertIn('AWS4-HMAC-SHA256',headers['Authorization'])
                return json.dumps({'Result':self.address}).encode()
            if 'CommitImageUpload' in url:
                self.assertEqual(method,'POST');self.assertIn('SessionKey=session%2Fkey',url)
                return json.dumps({'Result':self.commit}).encode()
            self.assertEqual((url,method,body),('https://example.volces.com/test/path','PUT',b'123456789'))
            self.assertEqual(headers['Content-CRC32'],'cbf43926');return b''
        with patch.object(images,'request',side_effect=request) as req:
            self.assertEqual(images.upload(self.window,self.path),'https://p3.byteimg.com/test.png')
        self.assertEqual(req.call_count,3)
        self.assertEqual([c.args[0] for c in self.window.call.call_args_list],['upload-token','image-url'])

    def test_invalid_host_does_not_receive_grants_or_image(self):
        for host in ['127.0.0.1','[::1]','example.volces.com@evil.example','example.volces.com:444','https://host.example/path','http://host.example','host.local','host.example#fragment']:
            with self.subTest(host=host):
                self.address['UploadAddress']['UploadHosts']=[host]
                self.window.call.side_effect=None;self.window.call.return_value={}
                with patch.object(images,'imagex',return_value=self.address),patch.object(images,'request') as request:
                    with self.assertRaises(DeliveryError):images.upload(self.window,self.path)
                    request.assert_not_called()

    def test_provider_host_formats_and_custom_domain(self):
        for host in ['upload.provider.example','https://upload.provider.example/','upload.provider.example:443']:
            self.assertEqual(images.provider_upload_url(host,'bucket/file.png'),'https://upload.provider.example/bucket/file.png')
        for uri in ['../escape','/other','https://other.example/a','bucket/a?token=x','bucket/../escape']:
            with self.assertRaises(DeliveryError):images.provider_upload_url('upload.provider.example',uri)

    def test_failed_upload_has_stage_and_does_not_resolve_image_url(self):
        with patch.object(images,'imagex',return_value=self.address),patch.object(images,'request',side_effect=DeliveryError('HTTP 403')):
            with self.assertRaisesRegex(DeliveryError,'上传图片文件失败'):images.upload(self.window,self.path)
        self.assertEqual(self.window.call.call_count,1)

    def test_missing_commit_confirmation_is_not_success(self):
        with patch.object(images,'imagex',side_effect=[self.address,{'Results':[]}]),patch.object(images,'request',return_value=b''):
            with self.assertRaisesRegex(DeliveryError,'确认图片上传失败'):images.upload(self.window,self.path)
        self.assertEqual(self.window.call.call_count,1)
