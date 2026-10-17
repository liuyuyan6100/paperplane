import tempfile
import unittest
from unittest.mock import Mock
from studio.service import Studio, Problem
from studio.adapters import DeliveryError
from studio.platform_diagnostics import diagnose


class PlatformDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.app = Studio(tmp.name); self.addCleanup(self.app.close)
        self.window = Mock(); self.window.call.return_value = {'authenticated': True, 'form_fields': []}
        self.app.publishing_bridge.browser = Mock(check_windows={'51cto': self.window, 'zhihu': self.window}, windows={'devto': self.window})

    def test_unknown_actions_and_extra_data_are_rejected_before_browser(self):
        for values in ({'channel':'51cto','action':'save'}, {'channel':'weibo'}, {'channel':'zhihu','image_id':'../secret'}, {'channel':'zhihu','image_id':123}):
            with self.subTest(values=values), self.assertRaises(Problem):diagnose(self.app,values)
        self.window.call.assert_not_called()

    def test_51_diagnose_only_no_job_or_verification_write(self):
        r = diagnose(self.app,{'channel':'51cto'})
        self.assertTrue(r['read_only'])
        self.window.call.assert_called_once_with('diagnose')
        self.assertFalse(self.app.jobs())
        self.assertFalse(self.app.db.rows("SELECT * FROM settings WHERE key LIKE 'platform-verification:%'"))
        self.assertFalse(self.app.publishing_bridge.serial.locked())

    def test_zhihu_existing_image_status_redacts_query_values(self):
        account = {'id':'known','name':'Fixture'}
        self.window.call.side_effect = [account, {'status':'success','src':'https://pica.zhimg.com/image.png?Signature=SECRET&Expires=12345'}, account]
        r = diagnose(self.app,{'channel':'zhihu','image_id':'12345'})
        self.assertEqual([x.args[0] for x in self.window.call.call_args_list],['identity','image-status','identity'])
        self.assertEqual(self.window.call.call_args_list[1].args,('image-status',{'id':'12345'}))
        self.assertNotIn('SECRET',str(r)); self.assertNotIn('Expires=12345',str(r))
        self.assertEqual(r['diagnostic']['src_shape']['host'],'pica.zhimg.com')

    def test_busy_or_exception_releases_locks(self):
        bridge = self.app.publishing_bridge;bridge.busy=True
        with self.assertRaises(Problem):diagnose(self.app,{'channel':'51cto'})
        self.window.call.assert_not_called();bridge.busy=False
        self.window.call.side_effect=DeliveryError('page unavailable')
        with self.assertRaises(Problem):diagnose(self.app,{'channel':'51cto'})
        self.assertFalse(bridge.serial.locked())

    def test_xhs_reads_existing_page_without_save_or_login(self):
        browser=Mock(); browser.diagnose.return_value={'available':False}
        self.app.xhs_drafts.browser=browser
        self.assertEqual(diagnose(self.app,{'channel':'xiaohongshu'})['diagnostic'],{'available':False})
        browser.diagnose.assert_called_once_with();browser.save_draft.assert_not_called();browser.login.assert_not_called()
        self.assertFalse(self.app.xhs_drafts.lock.locked())

    def test_zhihu_account_change_rejects_observation(self):
        self.window.call.side_effect=[{'id':'before'}, {'status':'success'}, {'id':'after'}]
        with self.assertRaises(Problem):diagnose(self.app,{'channel':'zhihu','image_id':'12345'})
        self.assertFalse(self.app.publishing_bridge.serial.locked())

if __name__ == '__main__':unittest.main()
