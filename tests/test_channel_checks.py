import tempfile
import unittest
from unittest.mock import Mock, patch
from studio.service import Studio, Problem
from studio import channel_checks
from studio.adapters import DeliveryError


class ChannelChecksTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=Studio(self.temp.name);self.addCleanup(self.app.close)

    def test_missing_config_does_not_claim_success(self):
        r=channel_checks.check(self.app,'wechat')
        self.assertEqual(r['action'],'settings')
        self.assertFalse(self.app.db.rows("SELECT * FROM settings WHERE key LIKE 'platform-verification:%'"))

    def test_wechat_repairs_mode_only_after_read_permission_passes(self):
        self.app.config.update({'WECHAT_APP_ID':'fixture','WECHAT_APP_SECRET':'fixture'})
        self.app.save_settings({'channels':[{'id':'wechat','enabled':False,'mode':'manual'}]})
        with patch('studio.wechat.test_connection',side_effect=DeliveryError('permission')):
            self.assertEqual(channel_checks.check(self.app,'wechat')['status'],'needs_config')
        row=next(c for c in self.app.channels() if c['id']=='wechat')
        self.assertEqual(row['config']['mode'],'manual')
        with patch('studio.wechat.test_connection',return_value={'ok':True}):
            self.assertEqual(channel_checks.check(self.app,'wechat')['status'],'passed')
        row=next(c for c in self.app.channels() if c['id']=='wechat')
        self.assertEqual(row['config']['mode'],'api');self.assertFalse(row['enabled'])
        self.assertEqual(row['verification']['draft']['status'],'unverified')
        self.assertFalse(self.app.jobs())

    def test_connected_channel_and_busy_are_not_delivery_proofs(self):
        window=Mock();window.call.return_value={'id':'123','name':'Test'}
        self.app.publishing_bridge.browser=Mock(windows={'toutiao':window})
        r=channel_checks.check(self.app,'toutiao')
        self.assertEqual(r['status'],'passed')
        window.call.assert_called_once_with('identity')
        self.app.publishing_bridge.busy=True
        self.assertEqual(channel_checks.check(self.app,'toutiao')['status'],'busy')
        self.assertFalse(self.app.db.rows("SELECT * FROM settings WHERE key LIKE 'platform-verification:%'"))

    def test_network_failure_preserves_cached_account(self):
        window=Mock();window.account={'id':'123','name':'Old'}
        window.call.side_effect=DeliveryError('network')
        self.app.publishing_bridge.browser=Mock(windows={'csdn':window})
        self.assertEqual(channel_checks.check(self.app,'csdn')['action'],'login')
        self.assertEqual(window.account['id'],'123')
        self.assertEqual(window.account_state,'unverified')

    def test_dev_api_and_browser_identity_must_match(self):
        self.app.config.update({'DEVTO_API_KEY':'fixture'})
        window=Mock()
        self.app.publishing_bridge.browser=Mock(windows={'devto':window})
        with patch('studio.channel_checks.request_json',return_value={'id':123}):
            for account,status in [(None,'needs_login'),({'id':'456','name':'Other'},'needs_login'),
                                   ({'id':'123','name':'Same'},'passed')]:
                with self.subTest(account=account):
                    window.call.return_value=account
                    outcome=channel_checks.check(self.app,'devto')
                    self.assertEqual(outcome['status'],status)
        self.assertTrue(all(args.args[0]=='identity' for args in window.call.call_args_list))
        self.assertFalse(self.app.jobs())

    def test_dev_key_changed_during_check_is_not_reported_passed(self):
        self.app.config.update({'DEVTO_API_KEY':'before'})
        def identity(*args,**kwargs):
            self.app.config.update({'DEVTO_API_KEY':'after'})
            return {'id':123}
        with patch('studio.channel_checks.request_json',side_effect=identity):
            self.assertEqual(channel_checks.check(self.app,'devto')['status'],'needs_config')

    def test_xhs_read_only_identity_wait_and_transient_error_preserves_account(self):
        browser=Mock();self.app.xhs_drafts.browser=browser
        account={'id':'known','name':'Known'}
        browser.verify_identity.return_value=account
        self.assertEqual(channel_checks.check(self.app,'xiaohongshu')['status'],'passed')
        browser.verify_identity.assert_called_once_with()
        browser.save_draft.assert_not_called()
        browser.verify_identity.side_effect=DeliveryError('page loading')
        self.assertEqual(channel_checks.check(self.app,'xiaohongshu')['status'],'needs_login')
        self.assertEqual(self.app.xhs_drafts.account,account)
        self.assertFalse(self.app.xhs_drafts.status()['logged_in'])
        self.assertEqual(self.app.xhs_drafts.status()['account_state'],'unverified')
        browser.verify_identity.side_effect=None;browser.verify_identity.return_value=None
        self.assertEqual(channel_checks.check(self.app,'xiaohongshu')['status'],'needs_login')
        self.assertIsNone(self.app.xhs_drafts.account)

    def test_reject_retired_and_concurrent_checks(self):
        with self.assertRaises(Problem):channel_checks.check(self.app,'wordpress')
        self.app.channel_check_lock.acquire()
        try:
            with self.assertRaises(Problem):channel_checks.check(self.app,'local')
        finally:self.app.channel_check_lock.release()
