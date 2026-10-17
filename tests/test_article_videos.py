"""Video evidence, draft playback identity and uncertain upload recovery."""
import base64
import copy
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import pytest

from studio.service import Studio,Problem
from studio import videos,wechat
from studio.adapters import DeliveryError
from studio.db import uid
from studio.markdown import render
from tests.test_wechat import PNG

def box(kind,data):return (8+len(data)).to_bytes(4,'big')+kind+data
MP4=box(b'ftyp',b'isom0000')+box(b'moov',b'test')+box(b'mdat',b'synthetic')

@pytest.fixture
def app(tmp_path):
    app=Studio(tmp_path)
    app.config.update({'AI_MODE':'api','AI_BASE_URL':'http://127.0.0.1:1/v1','AI_MODEL':'synthetic','WECHAT_APP_ID':'wx-test'})
    yield app
    app.close()

def add(app,post_id=None):
    post=app.post(post_id) if post_id else app.save_post({'title':'视频测试','body':'原有正文。','stage':'draft'})
    v=videos.upload(app,post['id'],{'title':'仿真回放','description':'30秒仿真；不是实机，不代表64环境统计。','filename':'sim.mp4','data':base64.b64encode(MP4).decode()})
    return post,v

def test_import_is_scoped_and_deduplicated(app):
    post,item=add(app);_,same=add(app,post['id']);other,second=add(app)
    assert item['id']==same['id'] and second['id']!=item['id']
    assert videos.listing(app,post['id'])==[item]
    assert app.post(post['id'])['body']=='原有正文。'
    assert (app.db.data_dir/'videos'/(item['id']+'.mp4')).read_bytes()==MP4
    assert 'data' not in json.dumps(videos.evidence([item]))

@pytest.mark.parametrize('data,name',[(b'<html>','a.mp4'),(box(b'ftyp',b'isom0000'),'a.mp4'),(MP4,'../a.mp4'),(MP4,'a.mov'),(b'\xff'*100,'a.mp4')])
def test_invalid_file_never_creates_video(app,data,name):
    p=app.save_post({'title':'稿件'})
    with pytest.raises(Problem):videos.upload(app,p['id'],{'title':'视频','description':'说明','filename':name,'data':base64.b64encode(data).decode()})
    assert videos.listing(app,p['id'])==[]

def test_metadata_revision_invalidates_old_context(app):
    post,v=add(app);old=app.editor_assistant.get('post',post['id'])
    videos.update(app,v['id'],{'version':1,'description':'用户补充：这是仿真视频。'})
    assert app.editor_assistant.get('post',post['id'])['revision']!=old['revision']
    with pytest.raises(Problem):videos.update(app,v['id'],{'version':1,'description':'旧页面'})

def test_ai_receives_description_not_video_bytes_or_other_post(app):
    post,v=add(app);add(app);captured=[]
    app.generate_draft=lambda post,*a,**k:(captured.append(json.loads(post['body'])) or json.dumps({'reply':'根据说明安排视频开场','replacement':None}))
    source=app.editor_assistant.get('post',post['id'])
    app.editor_assistant.send('post',post['id'],{'request_id':uid(),'revision':source['revision'],'draft':source['content'],'message':'如何围绕视频开场？','mode':'discuss'})
    app.editor_assistant.tick()
    assert len(captured[0]['videos'])==1
    assert captured[0]['videos'][0]['description']==v['description']
    assert 'wechat_media_id' not in json.dumps(captured) and base64.b64encode(MP4).decode() not in json.dumps(captured)

def test_outline_requires_reconfirmation_after_video_added(app):
    p=app.save_post({'title':'稿件','body':'原稿'})
    app.generate_draft=lambda *a,**k:'## 提纲\n说明演示'
    task=app.workflows.create({'post_id':p['id'],'version':p['version'],'instruction':'给出提纲','reference_ids':[]})
    app.workflows.tick();task=app.workflows.get(task['id']);add(app,p['id'])
    with pytest.raises(Problem,match='变化'):
        app.workflows.confirm(task['id'],{'outline':task['outline'],'outline_version':task['outline_version']})

@pytest.mark.parametrize('url',['https://evil.test/?vid=apiv_123456','javascript:evil','https://mp.weixin.qq.com.evil.test/mp/video?vid=wxv_123456','https://u:p@mp.weixin.qq.com/mp/video?vid=wxv_123456'])
def test_iframe_destination_is_never_caller_supplied(url):assert videos.player(url) is None

def test_native_material_readback_supplies_player_without_second_upload(app):
    _,v=add(app)
    with patch('studio.wechat.WeChatClient') as cls:
        cls.return_value.api.side_effect=[{'media_id':'accepted-video'},{'vid':'apiv_4741860341339586561'}]
        result=videos.prepare_wechat(app,v['id']);again=videos.prepare_wechat(app,v['id'])
        assert cls.return_value.api.call_count==2
        assert result['wechat_uploaded'] and videos.player(result['embed_url'])[1]=='apiv_4741860341339586561'
        assert again==result

def test_accepted_upload_survives_readback_failure_without_reupload(app):
    _,v=add(app)
    with patch('studio.wechat.WeChatClient') as cls:
        cls.return_value.api.side_effect=[{'media_id':'accepted-video'},DeliveryError('readback unavailable'),{'vid':'wxv_123456789'}]
        first=videos.prepare_wechat(app,v['id']);second=videos.prepare_wechat(app,v['id'])
        assert first['wechat_uploaded'] and not first['embed_url']
        assert second['embed_url']
        assert [c.args[0] for c in cls.return_value.api.call_args_list].count('/cgi-bin/material/add_material')==1

def test_uncertain_upload_cannot_be_replayed(app):
    _,v=add(app)
    with patch('studio.wechat.WeChatClient') as cls:
        cls.return_value.api.side_effect=DeliveryError('unknown acceptance',True)
        with pytest.raises(DeliveryError):videos.prepare_wechat(app,v['id'])
        with pytest.raises(DeliveryError,match='核对'):videos.prepare_wechat(app,v['id'])
        assert cls.return_value.api.call_count==1
    assert videos.get(app,v['id'])['status']=='uncertain'

def test_account_change_does_not_reuse_old_upload(app):
    _,v=add(app)
    with app.db.connect(immediate=True) as db:db.execute("UPDATE article_videos SET wechat_media_id='old',wechat_app_id='other' WHERE id=?",(v['id'],))
    with pytest.raises(DeliveryError,match='另一公众号'):videos.prepare_wechat(app,v['id'])

def test_preview_and_fenced_examples_preserve_video_semantics():
    marker='@[仿真 <说明>](/video/'+'a'*32+'.mp4)'
    assert '<video controls' in render(marker+'\n\n正文')
    assert '&lt;说明&gt;' in render(marker)
    assert '<video' not in render('```\n'+marker+'\n```')
    assert '<iframe' not in render('<iframe src="https://evil.test"></iframe>')

def test_wechat_player_readback_cannot_silently_drop_video(app):
    post,v=add(app);video=videos.update(app,v['id'],{'version':1,'embed_url':'https://mp.weixin.qq.com/mp/video?vid=wxv_123456789'})
    cover=app.db.data_dir/'media'/('b'*32+'.png');cover.parent.mkdir(exist_ok=True);cover.write_bytes(PNG)
    draft={**post,'cover':'/media/'+cover.name,'body':f'@[{v["title"]}]({v["url"]})\n\n正文。','_videos':[video]}
    prepared=wechat._prepare_post(draft,app.db.data_dir)
    assert '<iframe ' in prepared['content'] and prepared['content'].index('<iframe')<prepared['content'].index('正文')
    assert '<mpvideo' not in prepared['content']
    assert 'action=mpvideo' in prepared['content'] and 'height="360"' in prepared['content']
    assert 'data-mpvid="wxv_123456789"' in prepared['content']
    article={'title':post['title'],'author':'','digest':'','thumb_media_id':'thumb','content':prepared['content']}
    wechat._check_readback({'news_item':[article]},prepared)
    article['content']=article['content'].replace('wxv_123456789','wxv_987654321')
    with pytest.raises(DeliveryError,match='视频'):wechat._check_readback({'news_item':[article]},prepared)
    # A matching identity in an empty editor-only element is not a player.
    article['content']=prepared['content'].replace(videos.wechat_html(video),'<mpvideo vid="wxv_123456789"></mpvideo>')
    with pytest.raises(DeliveryError,match='视频'):wechat._check_readback({'news_item':[article]},prepared)
    draft['_videos']=[]
    with pytest.raises(DeliveryError,match='不属于'):wechat._prepare_post(draft,app.db.data_dir)

def test_unprepared_video_blocks_draft_before_network(app):
    post,v=add(app);post.update(body=f'@[{v["title"]}]({v["url"]})\n\n正文。')
    with patch('studio.wechat.WeChatClient') as cls:
        with pytest.raises(DeliveryError,match='播放器'):wechat.send_draft(post,{},app.config,app.db.data_dir)
        cls.assert_not_called()

def test_api_upload_identity_is_not_treated_as_a_working_article_player(app):
    post,v=add(app)
    video=videos.update(app,v['id'],{'version':1,'embed_url':'https://mp.weixin.qq.com/mp/video?vid=apiv_123456789'})
    assert not video['player_ready']
    cover=app.db.data_dir/'media'/('c'*32+'.png');cover.parent.mkdir(exist_ok=True);cover.write_bytes(PNG)
    post.update(body=f'@[{v["title"]}]({v["url"]})',cover='/media/'+cover.name,_videos=[video])
    with patch('studio.wechat.WeChatClient') as cls:
        with pytest.raises(DeliveryError,match='暂停自动送稿'):wechat.send_draft(post,{},app.config,app.db.data_dir)
        cls.assert_not_called()

@pytest.mark.parametrize('change',[
    lambda value:value.replace('height="360"','height="0"'),
    lambda value:value.replace('display:block','display:none'),
    lambda value:value.replace('data-mpvid="wxv_123456789"','data-mpvid="wxv_987654321"'),
    lambda value:value.replace('https://mp.weixin.qq.com/mp/readtemplate','https://evil.test/player'),
])
def test_preserved_video_id_does_not_hide_broken_player(change):
    original=videos.wechat_html({'embed_url':'https://mp.weixin.qq.com/mp/video?vid=wxv_123456789'})
    assert wechat._HTMLShape(original).video_players==[True]
    assert wechat._HTMLShape(change(original)).video_players==[False]

def test_backup_restore_preserves_video_bytes_and_evidence(app,tmp_path):
    from studio.migration import backup_bundle
    from tools.restore_studio import restore
    post,v=add(app);archive=tmp_path/'backup.zip';archive.write_bytes(backup_bundle(app))
    restored=tmp_path/'restored';restore(archive,restored)
    check=Studio(restored)
    try:
        assert videos.listing(check,post['id'])[0]['description']==v['description']
        assert (restored/'videos'/(v['id']+'.mp4')).read_bytes()==MP4
    finally:check.close()

def test_range_requests_and_private_api_guard(app):
    import threading,urllib.request,urllib.error
    from studio.server import Server
    _,v=add(app);server=Server(('127.0.0.1',0),app);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(base+v['url'],headers={'Range':'bytes=2-9'}),timeout=3) as response:
            assert response.status==206 and response.read()==MP4[2:10]
            assert response.headers['Content-Range']==f'bytes 2-9/{len(MP4)}'
        with pytest.raises(urllib.error.HTTPError) as exc:opener.open(urllib.request.Request(base+v['url'],headers={'Range':'bytes=999999-'}),timeout=3)
        assert exc.value.code==416
        with pytest.raises(urllib.error.HTTPError) as exc:opener.open(urllib.request.Request(base+'/api/videos/'+v['id'],data=b'{}',headers={'Content-Type':'application/json'}),timeout=3)
        assert exc.value.code==403
    finally:server.shutdown();server.server_close();thread.join(3)

def test_existing_material_association_verifies_vid_before_binding(app):
    _,v=add(app)
    with patch('studio.wechat.WeChatClient') as cls:
        cls.return_value.api.return_value={'vid':'wxv_1234567890123'}
        r=videos.associate_material(app,v['id'],{'media_id':'existing'})
        assert r['wechat_uploaded'] and r['embed_url'].endswith('wxv_1234567890123')
        assert cls.return_value.api.call_args.args[0]=='/cgi-bin/material/get_material'

NATIVE='<iframe class="video_iframe rich_pages" data-src="https://mp.weixin.qq.com/mp/readtemplate?t=pages/video_player_tmpl&amp;action=mpvideo&amp;auto=0&amp;vid=wxv_4742124293118164992" data-mpvid="wxv_4742124293118164992" data-vidtype="2" data-cover="http%3A%2F%2Fmmbiz.qpic.cn%2Fa%2F0" data-ratio="1.7777777777777777" data-w="960"></iframe>'

def native_job(app,post):
    from studio.db import dumps,now
    with app.db.connect(immediate=True) as db:
        db.execute("INSERT INTO jobs(id,run_id,post_id,channel_id,version,snapshot,status,due_at,created_at,updated_at,external_id) VALUES(?,?,?,?,?,?,'draft',?,?,?,?)",(uid(),uid(),post['id'],'wechat',post['version'],dumps(post),now(),now(),now(),'saved-draft'))

def test_native_editor_card_uses_ratio_viewport_and_whitelisted_fields():
    native=videos.native_players(NATIVE)[0]
    card=videos.wechat_html({'embed_url':'https://mp.weixin.qq.com/mp/video?vid='+native['vid'],'native_player':native})
    assert wechat._HTMLShape(card).video_players==[True]
    assert videos.native_players(card)==[native]
    assert 'height=' not in card and ' src=' not in card
    assert videos.native_players(NATIVE.replace('mmbiz.qpic.cn','evil.test'))==[]
    assert videos.native_players(NATIVE.replace('wxv_','apiv_'))==[]

def test_retained_api_html_does_not_prove_article_eligibility():
    native={**videos.native_players(NATIVE)[0],'vid':'apiv_4742124293118164992'}
    item={'embed_url':'https://mp.weixin.qq.com/mp/video?vid='+native['vid']}
    assert not videos.player_ready(item)
    item['native_player']=native
    assert not videos.player_ready(item)
    with pytest.raises(DeliveryError,match='自定义菜单'):videos.wechat_html(item)
    item['native_player']={**native,'cover':''}
    assert not videos.player_ready(item)
    with pytest.raises(DeliveryError):videos.wechat_html(item)

def test_associate_saved_native_video_changes_only_material_not_article_or_remote(app):
    post,v=add(app);post=app.save_post({'version':post['version'],'body':f'@[{v["title"]}]({v["url"]})'},post['id']);native_job(app,post)
    before=app.post(post['id'])
    with patch('studio.wechat.WeChatClient') as client:
        client.return_value.api.return_value={'news_item':[{'title':post['title'],'content':NATIVE}]}
        result=videos.associate_draft(app,v['id'],{'version':v['version']})
        assert [c.args[0] for c in client.return_value.api.call_args_list]==['/cgi-bin/draft/get']
    assert result['player_ready'] and result['native_player']['source']=='wechat-draft-readback'
    assert result['embed_url'].endswith('wxv_4742124293118164992')
    assert app.post(post['id'])==before
    assert videos.listing(app,post['id'])[0]['native_player']==result['native_player']

@pytest.mark.parametrize('content',[NATIVE+NATIVE,'<mpvideo vid="apiv_123456789"></mpvideo>',NATIVE.replace('data-w="960"','data-w="nan"')])
def test_native_association_never_guesses_missing_or_ambiguous_player(app,content):
    post,v=add(app);post=app.save_post({'version':post['version'],'body':f'@[{v["title"]}]({v["url"]})'},post['id']);native_job(app,post)
    with patch('studio.wechat.WeChatClient') as client:
        client.return_value.api.return_value={'news_item':[{'title':post['title'],'content':content}]}
        with pytest.raises(Problem):videos.associate_draft(app,v['id'],{'version':v['version']})
    assert videos.get(app,v['id'])['version']==v['version']

def test_native_association_rejects_stale_version_and_other_account(app):
    post,v=add(app)
    with pytest.raises(Problem):videos.associate_draft(app,v['id'],{'version':0})
    with app.db.connect(immediate=True) as db:db.execute("UPDATE article_videos SET wechat_app_id='other' WHERE id=?",(v['id'],))
    with pytest.raises(Problem):videos.associate_draft(app,v['id'],{'version':v['version']})

def test_native_card_and_local_video_survive_backup_restore(app,tmp_path):
    from studio.migration import backup_bundle
    from tools.restore_studio import restore
    from studio.db import dumps
    post,v=add(app);native=videos.native_players(NATIVE)[0]
    with app.db.connect(immediate=True) as db:
        db.execute('UPDATE article_videos SET embed_url=?,native_player=? WHERE id=?',('https://mp.weixin.qq.com/mp/video?vid='+native['vid'],dumps(native),v['id']))
    archive=tmp_path/'native.zip';archive.write_bytes(backup_bundle(app));folder=tmp_path/'restored-native';restore(archive,folder)
    check=Studio(folder)
    try:
        saved=videos.listing(check,post['id'])[0]
        assert saved['native_player']==native and saved['player_ready']
        assert (folder/'videos'/(v['id']+'.mp4')).read_bytes()==MP4
    finally:check.close()
