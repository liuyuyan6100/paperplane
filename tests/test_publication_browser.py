import unittest
from unittest.mock import Mock
from desktop_publishing import DesktopPublishingBrowser
from studio.adapters import DeliveryError

class PublicationBrowserTests(unittest.TestCase):
    def browser(self):
        b=DesktopPublishingBrowser();w=b.windows['juejin']
        w.account={'id':'owner','name':'Owner'}
        w.window=Mock();w.window.get_current_url.return_value='https://juejin.cn/editor/drafts/123'
        w._ensure_window=Mock();w.account_changed=Mock()
        return b,w

    def test_poll_lock_handoff_is_treated_as_background(self):
        from studio.publishing_bridge import PublishingBridge
        app=Mock();app.publication_sync.checking=False
        app.publication_sync.thread.is_alive.return_value=True
        bridge=PublishingBridge(app)
        self.assertTrue(bridge._background_checking())
        bridge.busy=True
        self.assertFalse(bridge._background_checking())
        bridge.busy=False;app.publication_sync.thread=None
        self.assertFalse(bridge._background_checking())

    def test_existing_window_is_not_shown_or_navigated(self):
        b,w=self.browser();result={'state':'published','draft_id':'123','article_id':'456','account_id':'owner','url':'https://juejin.cn/post/456'}
        w.call=Mock(side_effect=[w.account,result,w.account])
        self.assertEqual(b.check_publication('juejin','123','owner'),result)
        w._ensure_window.assert_called_once_with(hidden=True)
        w.window.show.assert_not_called();w.window.load_url.assert_not_called()

    def test_different_account_never_reads_article(self):
        b,w=self.browser();w.call=Mock(return_value={'id':'other','name':'Other'})
        with self.assertRaises(DeliveryError):b.check_publication('juejin','123','owner')
        w.call.assert_called_once_with('identity')

    def test_account_switch_during_check_discards_result(self):
        b,w=self.browser();w.call=Mock(side_effect=[w.account,{'state':'published'},{'id':'other','name':'Other'}])
        with self.assertRaises(DeliveryError):b.check_publication('juejin','123','owner')

    def test_missing_account_cache_still_checks_persisted_cookies_without_showing(self):
        b=DesktopPublishingBrowser();w=b.windows['juejin'];w._ensure_window=Mock();w.call=Mock(return_value=None)
        with self.assertRaises(DeliveryError):b.check_publication('juejin','123')
        w._ensure_window.assert_called_once_with(hidden=True)
        w.call.assert_called_once_with('identity')

    def test_success_redirect_uses_hidden_reader(self):
        b,w=self.browser();w.window.get_current_url.return_value='https://example.com/'
        reader=b.check_windows['juejin'];reader._ensure_window=Mock();reader.call=Mock(side_effect=[w.account,{'state':'draft'},w.account])
        self.assertEqual(b.check_publication('juejin','123','owner')['state'],'draft')
        reader._ensure_window.assert_called_once_with(hidden=True)
        w.window.load_url.assert_not_called();w._ensure_window.assert_not_called()

if __name__=='__main__':unittest.main()
