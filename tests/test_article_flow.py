"""Draft continuation operates only in temporary data and never contacts WeChat."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from studio.service import Studio, Problem
from studio.db import dumps, now

class ArticleFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Studio(Path(self.tmp.name) / 'data')
        self.addCleanup(self.app.close)
        self.app.config.update({'WECHAT_APP_ID':'synthetic','WECHAT_APP_SECRET':'synthetic'})
        with self.app.db.connect() as db:
            db.execute("UPDATE channels SET enabled=1,config=? WHERE id='wechat'",(dumps({'mode':'api'}),))
        self.check = patch('studio.wechat.validate_post',return_value=None)
        self.check.start(); self.addCleanup(self.check.stop)
    def post(self, **changes):
        return self.app.save_post({'title':'合成测试','body':'第一段。','summary':'合成摘要','author':'合成作者','stage':'draft',**changes})
    def request(self, post):
        return self.app.article_flow.request(post['id'],{'version':post['version']})
    def jobs(self): return self.app.db.rows('SELECT * FROM jobs')
    def test_single_request_prepares_stage_and_enqueues_without_extra_confirmation(self):
        post=self.post(); result=self.request(post)
        self.assertEqual(result['job']['status'],'queued')
        self.assertEqual(self.app.post(post['id'])['stage'],'ready')
        self.assertEqual(len(self.jobs()),1)
        self.assertEqual(json.loads(self.jobs()[0]['snapshot'])['delivery_config']['mode'],'api')
        self.assertEqual(result['delivery_model']['source'],'none')
    def test_missing_information_pauses_and_saved_correction_resumes_once(self):
        post=self.post(summary=''); result=self.request(post)
        self.assertEqual(result['intent']['status'],'waiting'); self.assertFalse(self.jobs())
        current=self.app.post(post['id'])
        self.app.save_post({'version':current['version'],'summary':'已补齐'},post['id'])
        self.app.article_flow.resume_due(); self.app.article_flow.resume_due()
        self.assertEqual(len(self.jobs()),1)
    def test_save_without_explicit_intent_never_sends(self):
        post=self.post(); self.app.save_post({'version':post['version'],'summary':'修改'},post['id'])
        self.app.article_flow.resume_due(); self.assertFalse(self.jobs())
    def test_cancel_waiting_stops_saved_correction(self):
        post=self.post(author=''); self.request(post); current=self.app.post(post['id'])
        self.app.article_flow.cancel(post['id'],{'version':current['version']})
        self.app.save_post({'version':current['version'],'author':'补齐'},post['id'])
        self.app.article_flow.resume_due(); self.assertFalse(self.jobs())
    def test_old_version_cannot_start_or_cancel(self):
        post=self.post(); self.app.save_post({'version':post['version'],'summary':'修改'},post['id'])
        with self.assertRaises(Problem): self.request(post)
        with self.assertRaises(Problem): self.app.article_flow.cancel(post['id'],{'version':post['version']})
        self.assertFalse(self.jobs())
    def test_repeated_start_and_saved_later_version_do_not_duplicate(self):
        post=self.post(); self.request(post); current=self.app.post(post['id'])
        current=self.app.save_post({'version':current['version'],'body':'再次修改'},post['id'])
        self.app.article_flow.resume_due(); result=self.request(current)
        self.assertEqual(len(self.jobs()),1); self.assertNotEqual(result['job']['version'],current['version'])
    def test_uncertain_and_failed_and_draft_jobs_are_never_automatically_retried(self):
        for status in ('uncertain','failed','draft','published'):
            post=self.post(); self.request(post)
            with self.app.db.connect() as db: db.execute('UPDATE jobs SET status=? WHERE post_id=?',(status,post['id']))
            current=self.app.post(post['id']); self.request(current)
            self.assertEqual(len([j for j in self.jobs() if j['post_id']==post['id']]),1)
    def test_placeholder_requires_correction(self):
        post=self.post(body='[待核实] 一个数据'); result=self.request(post)
        self.assertEqual(result['intent']['status'],'waiting'); self.assertFalse(self.jobs())
    def test_manual_wechat_mode_is_not_claimed_as_automatic_send(self):
        with self.app.db.connect() as db: db.execute("UPDATE channels SET config='{}' WHERE id='wechat'")
        result=self.request(self.post()); self.assertFalse(self.jobs())
        self.assertIn('公众号 API',str(result['intent']['report']))
    def test_formatting_repair_stored_in_only_job_snapshot(self):
        post=self.post(body='甲。\n\n\n\n\n乙。'); self.request(post)
        self.assertEqual(json.loads(self.jobs()[0]['snapshot'])['body'],self.app.post(post['id'])['body'])
        self.assertNotIn('\n\n\n\n',self.app.post(post['id'])['body'])
    def test_waiting_intent_survives_restart(self):
        post=self.post(summary=''); self.request(post); current=self.app.post(post['id'])
        self.app.save_post({'version':current['version'],'summary':'补齐'},post['id'])
        restarted=Studio(self.app.db.data_dir)
        self.addCleanup(restarted.close)
        restarted.article_flow.resume_due(); self.assertEqual(len(self.jobs()),1)
    def test_canceled_job_allows_explicit_retry_only(self):
        post=self.post(); self.request(post)
        with self.app.db.connect() as db: db.execute("UPDATE jobs SET status='canceled' WHERE post_id=?",(post['id'],))
        self.app.article_flow.resume_due(); self.assertEqual(len(self.jobs()),1)
        self.request(self.app.post(post['id'])); self.assertEqual(len(self.jobs()),2)
    def test_published_article_is_never_automatically_sent(self):
        post=self.post(stage='published')
        with self.assertRaises(Problem): self.request(post)
        self.assertFalse(self.jobs())
    def test_retained_delivery_tombstone_prevents_duplicate_across_versions(self):
        post=self.post()
        with self.app.db.connect() as db:
            db.execute('INSERT INTO delivery_tombstones(job_id,post_id,channel_id,version,status,purged_at) VALUES(?,?,?,?,?,?)',('old',post['id'],'wechat',1,'draft',now()))
        changed=self.app.save_post({'version':post['version'],'summary':'新版'},post['id'])
        result=self.request(changed); self.assertEqual(result['job']['id'],'old'); self.assertFalse(self.jobs())

    def test_cannot_claim_cancel_after_job_created(self):
        post=self.post(); self.request(post); current=self.app.post(post['id'])
        with self.assertRaises(Problem): self.app.article_flow.cancel(post['id'],{'version':current['version']})
    def test_archiving_waiting_article_stops_resume_after_restore(self):
        post=self.post(summary=''); self.request(post)
        self.app.archive(post['id'],True); self.app.archive(post['id'],False)
        current=self.app.post(post['id'])
        self.app.save_post({'version':current['version'],'summary':'补齐'},post['id'])
        self.app.article_flow.resume_due(); self.assertFalse(self.jobs())
    def test_channel_configuration_correction_resumes_without_body_edit(self):
        with self.app.db.connect() as db: db.execute("UPDATE channels SET config='{}' WHERE id='wechat'")
        self.request(self.post()); self.assertFalse(self.jobs())
        self.app.save_settings({'channels':[{'id':'wechat','enabled':True,'mode':'api'}]})
        self.app.article_flow.resume_due(); self.assertEqual(len(self.jobs()),1)

if __name__ == '__main__': unittest.main()
