import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from studio.service import Studio
from studio.adapters import DeliveryError, devto_send
from studio.devto_images import prepare

class DevtoImagesTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.app=Studio(temp.name);self.addCleanup(self.app.close)
        self.app.config.update({'DEVTO_API_KEY':'fixture-key'})
        self.browser=Mock();self.account={'id':'123','name':'Fixture'}
        self.browser.login.return_value=self.account
        self.browser.upload.return_value='https://dev-to-uploads.s3.amazonaws.com/uploads/articles/fixture.png'
        self.window=Mock();self.window.call.return_value=self.account
        self.browser.windows={'devto':self.window}
        self.app.publishing_bridge.browser=self.browser
        self.media='/media/'+'a'*32+'.png'
        root=Path(temp.name)/'media';root.mkdir();(root/Path(self.media).name).write_bytes(b'fixture')
        self.post={'title':'Test','body':'Body ![image]('+self.media+')','cover':self.media,'summary':'','tags':[]}

    def test_upload_same_image_once_and_preserve_original(self):
        with patch('studio.devto_images.request_json',return_value={'id':123}):
            result=prepare(self.app,self.post)
        self.browser.upload.assert_called_once()
        self.assertNotIn('/media/',result['body'])
        self.assertEqual(result['cover'],self.browser.upload.return_value)
        self.assertIn('/media/',self.post['body'])
        self.assertNotIn('fixture-key',str(result))

    def test_mismatched_browser_account_blocks_upload(self):
        with patch('studio.devto_images.request_json',return_value={'id':999}):
            with self.assertRaises(DeliveryError):prepare(self.app,self.post)
        self.browser.upload.assert_not_called()
        self.assertFalse(self.app.publishing_bridge.busy)

    def test_untrusted_url_or_changed_account_blocks_submission(self):
        self.browser.upload.return_value='https://evil.test/image.png'
        with patch('studio.devto_images.request_json',return_value={'id':123}):
            with self.assertRaises(DeliveryError):prepare(self.app,self.post)
        self.browser.upload.return_value='https://dev-to-uploads.s3.amazonaws.com/uploads/articles/fixture.png'
        self.window.call.return_value={'id':'999','name':'Other'}
        with patch('studio.devto_images.request_json',return_value={'id':123}):
            with self.assertRaises(DeliveryError):prepare(self.app,self.post)

    def test_key_changed_after_upload_never_sends_article(self):
        with patch('studio.devto_images.request_json',return_value={'id':123}):
            result=prepare(self.app,self.post)
        self.app.config.update({'DEVTO_API_KEY':'new-key'})
        with patch('studio.adapters.request_json') as request:
            with self.assertRaises(DeliveryError):devto_send(result,{'publish':False},self.app.config)
        request.assert_not_called()

    def test_official_media2_and_regional_s3_uploads(self):
        for url in ('https://media2.dev.to/dynamic/image/fixture.png','https://dev-to-uploads.s3.us-east-2.amazonaws.com/uploads/articles/fixture.png'):
            with self.subTest(url=url), patch('studio.devto_images.request_json',return_value={'id':123}):
                self.browser.upload.return_value=url
                self.assertEqual(prepare(self.app,self.post)['cover'],url)

    def test_regional_s3_namespace_still_restricted(self):
        for url in ('https://dev-to-uploads.s3.us-east-2.amazonaws.com/secrets','https://attacker.s3.us-east-2.amazonaws.com/uploads/articles/fixture.png','https://media2.dev.to.evil.test/image.png'):
            with self.subTest(url=url), patch('studio.devto_images.request_json',return_value={'id':123}):
                self.browser.upload.return_value=url
                with self.assertRaises(DeliveryError):prepare(self.app,self.post)

    def test_text_only_keeps_existing_api_flow(self):
        post={**self.post,'body':'Body','cover':''}
        self.assertIs(prepare(self.app,post),post)
        self.browser.login.assert_not_called()

if __name__=='__main__':unittest.main()
