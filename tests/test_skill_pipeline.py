"""Skill migration acceptance at real Studio/transport and persistence boundaries."""
import json
import logging
import os
import subprocess
from unittest.mock import patch

import pytest

from studio.db import uid
from studio.model_routing import route_for
from studio.service import Studio, Problem
from studio.skills import SkillSystem


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERPLANE_USE_SKILLS', '1')
    instance = Studio(tmp_path / 'data')
    instance.config.update({'AI_MODE':'api', 'AI_BASE_URL':'https://synthetic.invalid/v1',
                            'AI_MODEL':'synthetic', 'AI_API_KEY':'synthetic-key'})
    yield instance
    instance.close()


def test_real_pipeline_confirm_adopt_and_review(app):
    post = app.save_post({'title':'技术文章', 'body':'原稿保持不变', 'stage':'draft'})
    reference = app.save_post({'title':'参考', 'body':'SOURCE_CANARY', 'source':'https://example.test/source'})
    task = app.workflows.create({'post_id':post['id'], 'version':post['version'],
                                 'instruction':'REQUEST_CANARY', 'reference_ids':[reference['id']]})
    with patch('studio.adapters.ai_draft', return_value=json.dumps({'outline':'## 已选章节\n- 要点'})) as transport:
        app.workflows.tick()
        transport.assert_called_once()
        draft, instruction, config = transport.call_args.args
        assert draft['_model_route']['owner_id'] == task['id']
        assert draft['_writing_phase'] == 'outline'
        assert 'SOURCE_CANARY' in instruction and 'REQUEST_CANARY' in instruction
    task = app.workflows.get(task['id'])
    assert task['status'] == 'outline_review'
    with pytest.raises(Problem):
        app.workflows.apply(task['id'], {'version':post['version']})
    assert app.workflows.tick() is False
    app.workflows.confirm(task['id'], {'outline':task['outline'], 'outline_version':task['outline_version']})
    with patch('studio.adapters.ai_draft', return_value='生成的正文。') as transport:
        app.workflows.tick()
        transport.assert_called_once()
        assert transport.call_args.args[0]['_model_route']['task'] == 'body'
        assert task['outline'] in transport.call_args.args[1]
    done = app.workflows.get(task['id'])
    assert done['status'] == 'review'
    assert app.post(post['id'])['body'] == post['body']
    app.workflows.apply(task['id'], {'version':post['version']})
    post = app.post(post['id'])
    assert post['body'] == '生成的正文。'
    review = app.reviews.create({'post_id':post['id'], 'post_version':post['version'], 'request_id':uid()})
    with patch('studio.adapters.ai_draft', return_value=json.dumps({'summary':'候选建议', 'issues':[]})) as transport:
        app.reviews.tick()
        assert transport.call_args.kwargs['structured'] is True
        assert transport.call_args.args[0]['_model_route']['owner_id'] == review['id']
    assert app.reviews.get(review['id'])['status'] == 'review'
    assert app.post(post['id']) == post
    assert app.jobs() == []


@pytest.mark.parametrize('reply', [
    '{"outline":"", "political_risk_warning":"SYNTHETIC_RISK"}',
    '{"outline":"ok", "has_risk":true}',
    '{"outline":',
])
def test_bad_response_fails_without_legacy_retry_or_adoption(app, reply):
    post = app.save_post({'title':'测试', 'body':'保留原稿'})
    post = app.post(post['id'])
    task = app.workflows.create({'post_id':post['id'], 'version':post['version']})
    with patch('studio.adapters.ai_draft', return_value=reply) as transport:
        app.workflows.tick()
        transport.assert_called_once()
    done = app.workflows.get(task['id'])
    assert done['status'] == 'failed'
    assert done['error']
    assert not done['outline'] and not done['result']
    with pytest.raises(Problem):
        app.workflows.confirm(task['id'], {'outline':'绕过', 'outline_version':0})
    assert app.post(post['id']) == post
    assert app.jobs() == []


def test_changed_connection_rejected_before_transport(app):
    post = app.save_post({'title':'技术', 'body':'原稿'})
    route = route_for(app.config, 'outline')
    app.config.update({'AI_BASE_URL':'https://changed.invalid/v1'})
    with patch('studio.adapters.ai_draft') as transport:
        result = app.skill_system.execute('generate-outline', post=post, materials=[], model_route=route)
        transport.assert_not_called()
    assert not result['success']


def test_materials_preserve_order_versions_and_do_not_fetch(app):
    sources = [{'id':'b','title':'B','body':'B','version':3,'source':'https://example.test/b'},
               {'id':'a','title':'A','body':'A','version':1,'source':'https://example.test/a'}]
    with patch('studio.shared_library.request', side_effect=AssertionError('unexpected fetch')):
        result = app.skill_system.execute('extract-materials', materials=sources)
    assert result['success'] and result['data']['materials'] == sources
    result['data']['materials'][0]['body'] = 'mutated'
    assert sources[0]['body'] == 'B'
    assert not app.skill_system.execute('extract-materials', materials=sources, scope='web')['success']


@pytest.mark.parametrize('extra', [{'confirmed':False}, {'section':'不存在'}, {'style':'../private'}])
def test_expansion_requires_confirmed_scope(app, extra):
    inputs = {'post':{'id':'x','title':'测试'}, 'materials':[], 'model_route':route_for(app.config, 'body'),
              'outline':'## 章节一', 'confirmed':True, **extra}
    with patch('studio.adapters.ai_draft') as transport:
        result = app.skill_system.execute('expand-section', **inputs)
        transport.assert_not_called()
    assert result['error']['code'] == 'INVALID_INPUT'


def test_formatted_log_and_exception_redacted(caplog):
    logger = logging.getLogger('studio.skills')
    with caplog.at_level(logging.WARNING):
        try:
            raise ValueError('Authorization: Bearer SYNTHETIC_CANARY')
        except ValueError:
            logger.exception('Authorization: Bearer %s', 'SYNTHETIC_CANARY')
    assert 'SYNTHETIC_CANARY' not in caplog.text


def test_explicit_legacy_switch(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERPLANE_USE_SKILLS', '0')
    app = Studio(tmp_path / 'legacy')
    try:
        assert app.skill_system is None
    finally:
        app.close()


def test_prompt_directory_link_cannot_escape_skill_root(app, tmp_path):
    from studio.skill_executors import BaseExecutor
    from studio.skills import SkillError
    skill = tmp_path / 'skill'
    outside = tmp_path / 'outside'
    skill.mkdir(); outside.mkdir()
    (outside / 'secret.md').write_text('SYNTHETIC_PRIVATE_SOURCE', encoding='utf-8')
    link = skill / 'prompts'
    if os.name == 'nt':
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                        'New-Item -ItemType Junction -Path ' + quote(link) + ' -Target ' + quote(outside)
                        + ' -ErrorAction Stop | Out-Null'], check=True, capture_output=True)
    else:
        link.symlink_to(outside, target_is_directory=True)
    executor = BaseExecutor(app, {'path':skill, 'metadata':{'name':'test'}})
    with pytest.raises(SkillError) as caught:
        executor.load_resource('secret')
    assert caught.value.code == 'PROMPT_NOT_FOUND'
    assert 'SYNTHETIC_PRIVATE_SOURCE' not in str(caught.value)
