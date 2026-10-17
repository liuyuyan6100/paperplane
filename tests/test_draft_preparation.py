import json
from pathlib import Path
from unittest.mock import patch
import pytest
from studio.service import Studio,Problem
from studio import profiles,videos
from studio.db import dumps,now
from studio.draft_preparation import image_repairs
from studio.draft_preparation import normalize_summary
from tests.test_wechat import PNG

@pytest.fixture
def app(tmp_path):
 app=Studio(tmp_path);app.config.update({'AI_MODE':'api','AI_BASE_URL':'http://127.0.0.1:1/v1','AI_MODEL':'synthetic','WECHAT_APP_ID':'test','WECHAT_APP_SECRET':'test'})
 with app.db.connect(immediate=True) as db:db.execute("UPDATE channels SET config=?,enabled=1 WHERE id='wechat'",(dumps({'mode':'api'}),))
 app.generate_draft=lambda *a,**k:dumps({'summary':'根据正文生成的合成摘要，不涉及实机测试。'})
 with patch('studio.wechat.validate_post',return_value=None):yield app
 app.close()

def article(app,**values):
 profile=profiles.save(app,{'name':'Aiclaw · 技术实践','wechat_footer':'Aiclaw｜博客：https://blog.example/'})
 cover='a'*32+'.png';(app.db.data_dir/'media').mkdir(exist_ok=True);(app.db.data_dir/'media'/cover).write_bytes(PNG)
 return app.save_post({'title':'合成稿','body':'仿真演示，不是实机实验。\n\n'+profile['wechat_footer'],'stage':'draft','cover':'/media/'+cover,'profile_id':profile['id'],**values})

def request(app,post):return app.article_flow.request(post['id'],{'version':post['version']})
def finish(app,post):
 request(app,post);assert app.preparation.tick();return app.preparation.latest(post['id'])

def test_preparation_does_not_apply_until_confirmation_and_confirm_enqueues_once(app):
 post=article(app);flow=request(app,post);before=app.post(post['id'])
 assert flow['preparation']['status']=='queued' and not app.jobs()
 assert app.preparation.tick();proposal=app.preparation.latest(post['id'])
 assert proposal['status']=='review' and proposal['result']['author']=='Aiclaw'
 assert app.post(post['id'])==before and not app.jobs()
 result=app.preparation.confirm(proposal['id'],{'version':before['version'],'summary':'用户确认并修订的摘要','author':'Aiclaw'})
 assert result['job']['status']=='queued' and len(app.jobs())==1
 assert app.post(post['id'])['summary']=='用户确认并修订的摘要'
 app.preparation.confirm(proposal['id'],{'version':before['version']})
 assert len(app.jobs())==1

def test_repeated_check_and_poll_do_not_repeat_ai(app):
 post=article(app);request(app,post);current=app.post(post['id']);request(app,current)
 assert len(app.db.rows('SELECT * FROM draft_preparations'))==1
 with patch.object(app,'generate_draft',wraps=app.generate_draft) as model:
  assert app.preparation.tick();app.article_flow.resume_due();request(app,app.post(post['id']));assert not app.preparation.tick()
  assert model.call_count==1

def test_uploaded_api_material_is_pending_in_unified_review(app):
 post=article(app);video_id='d'*32
 with app.db.connect(immediate=True) as db:
  db.execute("INSERT INTO article_videos(id,post_id,title,description,filename,sha256,bytes,wechat_media_id,wechat_app_id,embed_url,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(video_id,post['id'],'仿真','说明','a.mp4','f'*64,1,'existing-material','test','https://mp.weixin.qq.com/mp/video?vid=apiv_123456789','uploaded',now(),now()))
 post=app.save_post({'version':post['version'],'body':post['body']+'\n\n@[仿真](/video/'+video_id+'.mp4)'},post['id'])
 with patch('studio.wechat.WeChatClient') as client:
  app.preparation.request(post['id']);assert app.preparation.tick()
  client.assert_not_called()
 result=app.preparation.latest(post['id'])['result']
 assert result['videos'][0]['ready'] is False and result['pending']
 assert not app.jobs()

def test_local_video_preparation_never_uploads_menu_reply_material(app):
 post=article(app);video_id='e'*32
 with app.db.connect(immediate=True) as db:
  db.execute("INSERT INTO article_videos(id,post_id,title,description,filename,sha256,bytes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(video_id,post['id'],'口语男声仿真','说明','voice.mp4','f'*64,1,now(),now()))
 post=app.save_post({'version':post['version'],'body':post['body']+'\n\n@[仿真](/video/'+video_id+'.mp4)'},post['id'])
 with patch('studio.wechat.WeChatClient') as client,patch('studio.videos.prepare_wechat') as upload:
  app.preparation.request(post['id']);assert app.preparation.tick()
  client.assert_not_called();upload.assert_not_called()
 result=app.preparation.latest(post['id'])['result']
 assert result['videos'][0]['ready'] is False
 assert any('本地上传' in message for message in result['pending'])
 assert videos.get(app,video_id)['wechat_media_id']=='' and not app.jobs()

def test_restore_image_only_from_same_article_history_and_preserve_code(app):
 post=article(app,summary='完整摘要',author='Aiclaw')
 correct='/media/'+'b'*32+'.png';wrong='/media/'+'c'*32+'.png';(app.db.data_dir/'media'/Path(correct).name).write_bytes(PNG)
 post=app.save_post({'version':post['version'],'body':f'![现场图]({correct})'},post['id'])
 post=app.save_post({'version':post['version'],'body':f'![现场图]({wrong})\n\n```\n![现场图]({wrong})\n```'},post['id'])
 body,repairs,pending=image_repairs(app,post)
 assert body.startswith(f'![现场图]({correct})') and f'```\n![现场图]({wrong})' in body
 assert len(repairs)==1 and not pending
 proposal=finish(app,post)
 assert proposal['result']['body'].startswith(body) and proposal['result']['footer_added'] and app.post(post['id'])['body']==post['body']

def test_other_article_image_not_used_as_replacement(app):
 post=article(app);correct='/media/'+'b'*32+'.png';(app.db.data_dir/'media'/Path(correct).name).write_bytes(PNG)
 app.save_post({'title':'别篇','body':f'![相同说明]({correct})'})
 post=app.save_post({'version':post['version'],'body':'![相同说明](/media/'+'c'*32+'.png)'},post['id'])
 body,repairs,pending=image_repairs(app,post)
 assert not repairs and pending and body==post['body']

def test_changed_article_or_profile_blocks_confirmation(app):
 post=article(app);proposal=finish(app,post);current=app.post(post['id'])
 app.save_post({'version':current['version'],'body':'后续手改内容'},post['id'])
 with pytest.raises(Problem):app.preparation.confirm(proposal['id'],{'version':current['version']})
 assert not app.jobs() and app.post(post['id'])['body']=='后续手改内容'

def test_cancel_during_model_keeps_original_and_skips_video_upload(app):
 post=article(app);request(app,post)
 def model(*args,**kwargs):
  current=app.post(post['id']);app.article_flow.cancel(post['id'],{'version':current['version']});return dumps({'summary':'合成摘要'})
 app.generate_draft=model
 with patch('studio.videos.prepare_wechat') as upload:app.preparation.tick();upload.assert_not_called()
 assert app.preparation.latest(post['id'])['status']=='canceled' and not app.post(post['id'])['summary'] and not app.jobs()

def test_manual_saved_corrections_invalidate_old_preparation_without_ai_call(app):
 post=article(app);request(app,post);current=app.post(post['id'])
 app.save_post({'version':current['version'],'summary':'手工补齐摘要','author':'作者'},post['id'])
 app.article_flow.resume_due()
 with patch.object(app,'generate_draft',side_effect=AssertionError('No obsolete model call')):assert not app.preparation.tick()
 assert len(app.jobs())==1

def test_old_waiting_intent_prepares_once_after_upgrade(app):
 post=article(app,stage='ready')
 with app.db.connect(immediate=True) as db:db.execute("INSERT INTO article_draft_intents(post_id,checked_version,updated_at) VALUES(?,?,?)",(post['id'],post['version'],now()))
 app.article_flow.resume_due();app.article_flow.resume_due()
 assert len(app.db.rows('SELECT * FROM draft_preparations'))==1 and not app.jobs()

def test_unknown_author_must_be_filled_by_user_not_invented_by_model(app):
 post=app.save_post({'title':'未关联画像','body':'合成正文','stage':'draft'})
 app.generate_draft=lambda *a,**k:dumps({'summary':'合成摘要','author':'模型杜撰的作者'})
 proposal=finish(app,post)
 assert proposal['result']['author']==''
 with pytest.raises(Problem):app.preparation.confirm(proposal['id'],{'version':proposal['source_version']})
 assert not app.jobs()

def test_failed_preparation_not_automatically_retried(app):
 post=article(app);request(app,post);app.generate_draft=lambda *a,**k:'bad JSON'
 app.preparation.tick();prior=app.preparation.latest(post['id']);assert prior['status']=='failed'
 app.article_flow.resume_due();request(app,app.post(post['id']));assert not app.preparation.tick()
 retry=app.preparation.request(post['id'],{'version':app.post(post['id'])['version'],'retry':True})
 assert retry['id']!=prior['id'] and retry['status']=='queued'

def test_archived_or_modified_queued_article_does_not_call_model(app):
 post=article(app);request(app,post);app.archive(post['id'],True)
 with patch.object(app,'generate_draft',side_effect=AssertionError('No archived model call')):assert app.preparation.tick()
 assert app.preparation.latest(post['id'])['status']=='stale'

def test_profile_change_after_preparation_blocks_confirmation(app):
 post=article(app);proposal=finish(app,post);profile=profiles.get(app,post['profile_id'])
 profiles.save(app,{**profile,'name':'另一个署名'},profile['id'])
 with pytest.raises(Problem):app.preparation.confirm(proposal['id'],{'version':proposal['source_version']})
 assert not app.jobs() and app.post(post['id'])['author']==''

def test_backup_restore_never_replays_pending_preparation_or_intent(app,tmp_path):
 from studio.migration import backup_bundle
 from tools.restore_studio import restore
 post=article(app);request(app,post);bundle=backup_bundle(app);archive=tmp_path/'prep.zip';archive.write_bytes(bundle)
 restored=tmp_path/'restored';restore(archive,restored);other=Studio(restored)
 try:
  assert other.preparation.latest(post['id'])['status']=='interrupted'
  assert other.article_flow.get(post['id'])['intent']['status']=='canceled'
  with patch.object(other,'generate_draft',side_effect=AssertionError('No restored model call')):assert not other.preparation.tick()
 finally:other.close()

def test_overlong_summary_is_shortened_for_review_without_another_model_call(app):
 post=article(app);request(app,post)
 raw='仿真演示只能反映当前场景，不代表实机实验结果。'*12
 with patch.object(app,'generate_draft',return_value=dumps({'summary':raw})) as model:
  app.preparation.tick();proposal=app.preparation.latest(post['id'])
  assert proposal['status']=='review' and len(proposal['result']['summary'])<=120
  assert proposal['result']['original_summary']==raw and proposal['result']['summary_shortened']
  assert model.call_count==1 and not app.post(post['id'])['summary'] and not app.jobs()

def test_summary_compatibility_never_treats_article_body_as_a_summary():
 assert normalize_summary({'摘要':'明确的中文摘要'})[0]=='明确的中文摘要'
 assert len(normalize_summary({'summary':'字'*200})[0])<=120
 with pytest.raises(Exception):normalize_summary({'body':'正文不是摘要'})
