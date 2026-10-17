import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.service import Studio, Problem
from studio import platform_catalog as catalog
from studio.db import dumps
from tests.test_repurpose import content


class PlatformCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Studio(Path(self.temp.name))
        self.addCleanup(self.app.close)

    def row(self, key):
        return next(p for p in self.app.channels() if p['id'] == key)

    def test_catalog_migration_preserves_existing_config(self):
        with self.app.db.connect() as db:
            db.execute("UPDATE channels SET enabled=0, config=? WHERE id='devto'", (dumps({'publish':True}),))
        from studio.db import Database
        Database(self.temp.name)
        row=self.row('devto')
        self.assertFalse(row['enabled'])
        self.assertTrue(row['config']['publish'])
        self.assertEqual(len(self.app.channels()), len(catalog.CATALOG))
        self.assertFalse(self.row('zhihu')['enabled'])

    def test_enabled_platform_generates_full_variant_and_cards(self):
        post=self.app.save_post({'title':'母稿','body':'可核实的原始事实','stage':'draft'})
        args={'post_id':post['id'],'post_version':1,'platforms':['zhihu'],'visual_mode':'cards'}
        with self.assertRaises(Problem): self.app.repurpose.create(args)
        self.app.save_settings({'channels':[{'id':'zhihu','enabled':True}]})
        task=self.app.repurpose.create(args)['variants'][0]
        with patch.object(self.app,'generate_draft',return_value=dumps(content())):
            self.assertTrue(self.app.repurpose.tick())
        while self.app.deliverables.tick(): pass
        task=self.app.repurpose.get(task['id'])
        self.assertEqual(task['status'],'review')
        adopted=self.app.repurpose.apply(task['id'],{'version':task['version'],'publish_public':True})
        self.assertEqual(adopted['post']['target_channel'],'zhihu')
        self.assertFalse(self.app.jobs())
        self.assertFalse(self.row('zhihu')['auto_send'])
        self.assertIn('zhihu', catalog.enabled_repurpose(self.app))
        self.assertIn('zhihu / 知乎：已启用',catalog.chat_context(self.app))

    def test_manual_history_is_never_send_verification(self):
        from studio.publication_progress import register
        post=self.app.save_post({'title':'历史文章','body':'正文','stage':'ready'})
        register(self.app,post['id'],{'version':1,'channels':['devto'],'confirmed':True})
        self.assertFalse(self.row('devto')['auto_send'])
        self.assertEqual(self.row('devto')['verification']['publish']['status'],'unverified')

    def test_real_adapter_receipt_promotes_only_its_operation_and_credential(self):
        self.app.config.update({'DEVTO_API_KEY':'isolated-test-key'})
        post=self.app.save_post({'title':'Test','body':'Body','summary':'Summary','stage':'ready'})
        self.app.enqueue(post['id'],{'version':1,'channels':['devto'],'ack_warnings':True})
        receipt={'status':'draft','external_id':'test-only','url':'https://dev.to/test/test-only','note':'test receipt'}
        with patch('studio.adapters.devto_send',return_value=receipt): self.app.tick()
        row=self.row('devto')
        self.assertTrue(row['auto_send'])
        self.assertEqual(row['verification']['publish']['status'],'unverified')
        self.app.save_settings({'channels':[{'id':'devto','enabled':True,'publish':True}]})
        self.assertFalse(self.row('devto')['auto_send'])
        self.app.config.update({'DEVTO_API_KEY':'another-test-account'})
        self.assertEqual(self.row('devto')['verification']['draft']['status'],'unverified')
        self.assertNotIn('fingerprint',dumps(self.app.channels()))

    def test_unknown_result_does_not_promote(self):
        with self.app.db.connect() as db:
            catalog.record_success(self.app,db,'csdn',{'status':'uncertain','external_id':'test'},'test','test')
        self.assertEqual(self.row('csdn')['verification']['draft']['status'],'unverified')

    def test_remembered_login_is_not_verified_or_enabled(self):
        status = dict(enabled=True, connected=False, desktop=True, busy=False,
                      accounts={'zhihu':{'id':'fixture','name':'测试账号'}},
                      account_states={'zhihu':'remembered'})
        with patch.object(self.app.publishing_bridge, 'status', return_value=status):
            row = self.row('zhihu')
            self.assertIn('待核验', row['account'])
            self.assertNotIn('已登录', row['account'])
            self.assertFalse(row['enabled'])
            self.assertTrue(row['desktop_login'])
            self.assertEqual(row['verification']['draft']['status'], 'unverified')
            status['account_states']['zhihu'] = 'verified'
            row = self.row('zhihu')
            self.assertIn('已登录', row['account'])
            self.assertFalse(row['enabled'])
            self.assertEqual(row['verification']['draft']['status'], 'unverified')

    def test_retired_channels_preserve_rows_but_cannot_be_selected(self):
        with self.app.db.connect() as db:
            for key in catalog.RETIRED_PLATFORMS:
                db.execute('INSERT INTO channels(id,name,kind,enabled,config) VALUES(?,?,?,?,?)', (key,key,'manual',1,'{}'))
        from studio.db import Database
        Database(self.temp.name)
        self.assertFalse(catalog.RETIRED_PLATFORMS & {r['id'] for r in self.app.channels()})
        for key in catalog.RETIRED_PLATFORMS:
            self.assertEqual(self.app.db.one('SELECT enabled FROM channels WHERE id=?',(key,))['enabled'],0)
            with self.assertRaises(Problem):
                self.app.save_settings({'channels':[{'id':key,'enabled':True}]})

    def test_browser_login_never_promotes_draft_delivery(self):
        status = dict(enabled=True,connected=False,desktop=True,busy=False,
                      accounts={p:{'id':'123','name':'Test'} for p in catalog.BRIDGE_PLATFORMS},
                      account_states={p:'verified' for p in catalog.BRIDGE_PLATFORMS})
        with patch.object(self.app.publishing_bridge,'status',return_value=status):
            for key in catalog.BRIDGE_PLATFORMS:
                row=self.row(key)
                self.assertTrue(row['desktop_login'])
                self.assertFalse(row['login_only'])
                self.assertTrue(row['delivery_implemented'])
                self.assertFalse(row['auto_send'])


if __name__=='__main__': unittest.main()
