import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from desktop_publishing import DesktopPublishingBrowser
from desktop_xhs import DesktopXhsBrowser
from studio.adapters import DeliveryError


class PlatformLoginTests(unittest.TestCase):
    def test_oschina_login_redirect_origin(self):
        window = DesktopPublishingBrowser().windows['oschina']
        window.window = Mock()
        for url in ('https://my.oschina.net/', 'https://www.oschina.net/'):
            window.window.get_current_url.return_value = url
            window._check()
        for url in ('http://www.oschina.net/', 'https://www.oschina.net.evil.test/',
                    'https://www.oschina.net:444/', 'https://evil.test/',
                    'https://www.oschina.net@evil.test/'):
            with self.subTest(url=url):
                window.window.get_current_url.return_value = url
                with self.assertRaises(DeliveryError):
                    window._check()

    def test_window_close_error_keeps_platform_and_uncertainty(self):
        window = DesktopPublishingBrowser().windows['oschina']
        with patch.object(DesktopXhsBrowser, '_wait',
                          side_effect=DeliveryError('小红书窗口已关闭', uncertain=True)):
            with self.assertRaises(DeliveryError) as caught:
                window._wait(lambda: None, 'wait')
        self.assertIn('开源中国', str(caught.exception))
        self.assertNotIn('小红书', str(caught.exception))
        self.assertTrue(caught.exception.uncertain)

    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/'platform-accounts.json'
        self.account={'id':'fixture','name':'测试账号'}

    def test_verified_account_restores_as_remembered_not_verified(self):
        browser=DesktopPublishingBrowser(self.path);window=browser.windows['juejin']
        window._ensure_window=Mock();window.call=Mock(return_value=self.account)
        progress=Mock()
        self.assertEqual(browser.login('juejin',progress),self.account)
        window.call.assert_called_once_with('identity')
        self.assertEqual(browser.account_states()['juejin'],'verified')
        saved=json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['juejin'],self.account)
        restored=DesktopPublishingBrowser(self.path)
        self.assertEqual(restored.accounts()['juejin'],self.account)
        self.assertEqual(restored.account_states()['juejin'],'remembered')

    def test_network_error_keeps_identity_but_cannot_authenticate(self):
        self.path.write_text(json.dumps({'juejin':self.account}),encoding='utf-8')
        browser=DesktopPublishingBrowser(self.path);window=browser.windows['juejin']
        window._ensure_window=Mock();window.call=Mock(side_effect=DeliveryError('network'))
        window._wait=Mock(side_effect=DeliveryError('not verified'))
        with self.assertRaises(DeliveryError):browser.login('juejin',Mock())
        self.assertEqual(browser.accounts()['juejin'],self.account)
        self.assertEqual(browser.account_states()['juejin'],'unverified')

    def test_explicit_signed_out_clears_stale_identity(self):
        self.path.write_text(json.dumps({'juejin':self.account}),encoding='utf-8')
        browser=DesktopPublishingBrowser(self.path);window=browser.windows['juejin']
        window._ensure_window=Mock();window.call=Mock(return_value=None)
        window._wait=Mock(side_effect=DeliveryError('signed out'))
        with self.assertRaises(DeliveryError):browser.login('juejin',Mock())
        self.assertIsNone(DesktopPublishingBrowser(self.path).accounts()['juejin'])

    def test_corrupt_cache_does_not_prevent_start(self):
        self.path.write_text('{broken',encoding='utf-8')
        self.assertIsNone(DesktopPublishingBrowser(self.path).accounts()['juejin'])
