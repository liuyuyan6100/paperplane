import tempfile
import unittest
from pathlib import Path
from studio.service import Studio, Problem
from studio import profiles
from studio.profile_sync import ProfileSync, exchange, digest, put, media


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.a = Studio(Path(self.temp.name)/'a')
        self.b = Studio(Path(self.temp.name)/'b')
        self.a.config.update({'PROFILE_SYNC_URL': 'https://example.com'})
        self.sync = ProfileSync(self.a, lambda v: exchange(self.b, v))

    def tearDown(self):
        self.a.close(); self.b.close(); self.temp.cleanup()

    def test_bidirectional_identity_and_no_task_replication(self):
        p = profiles.save(self.b, {'name': '原画像', 'memory': '云端反馈'})
        self.assertEqual(self.sync.run()['state'], 'synced')
        local = profiles.get(self.a, p['id'])
        self.assertEqual(digest(local), digest(p))
        profiles.save(self.a, {'version': local['version'], 'memory': '本机反馈'}, p['id'])
        self.assertEqual(self.sync.run()['state'], 'synced')
        self.assertEqual(profiles.get(self.b, p['id'])['memory'], '本机反馈')
        self.sync.run()
        self.assertEqual(len(profiles.listing(self.a)), 1)
        self.assertEqual(len(self.b.state()['posts']), 0)

    def test_offline_both_edit_conflict_stale_and_merge(self):
        p = profiles.save(self.b, {'name': '画像'})
        self.sync.run()
        l = profiles.get(self.a, p['id'])
        profiles.save(self.a, {'version': l['version'], 'memory': '本机'}, p['id'])
        profiles.save(self.b, {'version': p['version'], 'memory': '云端'}, p['id'])
        c = self.sync.run()['conflicts'][0]
        self.assertEqual(profiles.get(self.a, p['id'])['memory'], '本机')
        args = {k: c[k] for k in ('id','local_hash','remote_hash')}
        self.sync.resolve({**args, 'choice': 'merge', 'profile': {**c['local'], 'memory': '合并反馈'}})
        self.assertEqual(profiles.get(self.b, p['id'])['memory'], '合并反馈')
        with self.assertRaises(Problem): self.sync.resolve({**args, 'choice': 'remote'})

    def test_cas_and_retry_after_lost_ack(self):
        p = profiles.save(self.b, {'name': '画像'})
        with self.assertRaises(Problem): put(self.b, p['id'], {**p,'memory':'坏覆盖'}, None)
        result = put(self.b, p['id'], {**p,'memory':'新反馈'}, digest(p))
        again = put(self.b, p['id'], {**p,'memory':'新反馈'}, digest(p))
        self.assertEqual(again['version'], result['version'])

    def test_reference_images_integrity_and_collision(self):
        import io, base64, hashlib
        from PIL import Image
        buf=io.BytesIO();Image.new('RGB',(4,4),'red').save(buf,format='PNG');raw=buf.getvalue()
        ref='/media/'+ 'a'*32+'.png'
        blob={'op':'put_media','url':ref,'data':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest()}
        media(self.b,blob)
        p=profiles.save(self.b,{'name':'图画像','visual_references':[ref]})
        self.assertEqual(self.sync.run()['state'],'synced')
        self.assertEqual((self.a.db.data_dir/ref.lstrip('/')).read_bytes(), raw)
        with self.assertRaises(Problem): media(self.a,{**blob,'url':'/media/../../private.json'})
        with self.assertRaises(Problem): media(self.a,{**blob,'sha256':'x'})

    def test_offline_recovery_and_no_credentials_in_status(self):
        p=profiles.save(self.a,{'name':'离线画像'})
        self.sync.transport=lambda v: (_ for _ in ()).throw(OSError('offline secret'))
        status=self.sync.run()
        self.assertEqual(status['state'],'offline'); self.assertNotIn('secret',str(status))
        self.assertEqual(profiles.get(self.a,p['id'])['name'],'离线画像')
        self.sync.transport=lambda v:exchange(self.b,v)
        self.assertEqual(self.sync.run()['state'],'synced')

    def test_concurrent_change_during_resolution_preserved(self):
        p=profiles.save(self.b,{'name':'画像'});self.sync.run()
        l=profiles.get(self.a,p['id'])
        profiles.save(self.a,{'version':l['version'],'style':'本机'},p['id'])
        profiles.save(self.b,{'version':p['version'],'style':'云端'},p['id'])
        c=self.sync.run()['conflicts'][0]
        newer=profiles.get(self.b,p['id'])
        profiles.save(self.b,{'version':newer['version'],'style':'云端再改'},p['id'])
        with self.assertRaises(Problem):self.sync.resolve({**{k:c[k] for k in ('id','local_hash','remote_hash')},'choice':'local'})
        self.assertEqual(profiles.get(self.b,p['id'])['style'],'云端再改')

    def test_config_change_does_not_forward_old_credentials(self):
        self.a.config.update({'PROFILE_SYNC_CLIENT_ID':'secret-id','PROFILE_SYNC_CLIENT_SECRET':'secret'})
        self.sync.configure({'server':'https://other.example.com'})
        self.assertEqual(self.a.config.get('PROFILE_SYNC_CLIENT_SECRET'),'')
        with self.assertRaises(Problem):self.sync.configure({'server':'http://localhost'})

if __name__=='__main__': unittest.main()
