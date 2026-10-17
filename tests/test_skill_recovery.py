"""Phase 2 recovery through actual Studio managers and model transport boundary."""
import json
from unittest.mock import patch

import pytest

from studio.adapters import DeliveryError
from studio.db import uid
from studio.service import Problem, Studio


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERPLANE_USE_SKILLS', '1')
    instance = Studio(tmp_path / 'data')
    instance.config.update({'AI_MODE': 'api', 'AI_MODEL': 'synthetic',
                            'AI_BASE_URL': 'https://synthetic.invalid/v1'})
    yield instance
    instance.close()


def create(app):
    post = app.save_post({'title': '恢复验收', 'body': '原始正文', 'stage': 'draft'})
    flow = app.workflows.create({'post_id': post['id'], 'version': post['version']})
    return app.post(post['id']), flow


@pytest.mark.parametrize('phase', ['outline', 'body', 'review'])
@pytest.mark.parametrize('retryable,recovers,expected_calls', [(True, True, 2), (True, False, 2), (False, False, 1)])
def test_transport_retry_is_owned_by_studio(app, phase, retryable, recovers, expected_calls):
    post, flow = create(app)
    manager = app.workflows
    if phase != 'outline':
        with patch('studio.adapters.ai_draft', return_value='## 已确认章节'):
            manager.tick()
        flow = manager.get(flow['id'])
        manager.confirm(flow['id'], {'outline': flow['outline'], 'outline_version': flow['outline_version']})
    if phase == 'review':
        with patch('studio.adapters.ai_draft', return_value='独立测试正文。'):
            manager.tick()
        manager.apply(flow['id'], {'version': post['version']})
        post = app.post(post['id'])
        flow = app.reviews.create({'post_id': post['id'], 'post_version': post['version'], 'request_id': uid()})
        manager = app.reviews
    output = {'outline': '## 重试成功', 'body': '重试成功正文。',
              'review': json.dumps({'summary': '可阅读', 'issues': []})}[phase]
    failure = DeliveryError('合成暂时故障' if retryable else '合成认证失败', retryable=retryable)
    effects = [failure, output if recovers else failure]
    with patch('studio.adapters.ai_draft', side_effect=effects) as transport:
        manager.tick()
        assert transport.call_count == expected_calls
    task = manager.get(flow['id'])
    expected = ('outline_review' if phase == 'outline' else 'review') if recovers else 'failed'
    assert task['status'] == expected
    assert app.post(post['id']) == post
    assert app.jobs() == []
    assert manager.tick() is False
    if not recovers:
        # Recovery is explicitly requested; it cannot happen from the scheduler.
        if phase == 'review':
            manager.retry(flow['id'], {})
        else:
            manager.retry(flow['id'])
        with patch('studio.adapters.ai_draft', return_value=output) as transport:
            manager.tick()
            transport.assert_called_once()
        assert manager.get(flow['id'])['status'] == ('outline_review' if phase == 'outline' else 'review')


def test_restart_keeps_confirmed_outline_and_manual_retry(app, tmp_path):
    post, flow = create(app)
    with patch('studio.adapters.ai_draft', return_value='## 保留已确认章节'):
        app.workflows.tick()
    task = app.workflows.get(flow['id'])
    app.workflows.confirm(task['id'], {'outline': task['outline'], 'outline_version': task['outline_version']})
    # Simulate a process dying after claiming a body job, before persisting output.
    with app.db.connect(immediate=True) as db:
        db.execute("UPDATE workflows SET status='running',stage='generate',run_token='dead-process' WHERE id=?", (task['id'],))
    app.close()
    restarted = Studio(tmp_path / 'data')
    try:
        with patch('studio.adapters.ai_draft') as transport:
            assert restarted.workflows.get(task['id'])['status'] == 'interrupted'
            assert restarted.workflows.tick() is False
            transport.assert_not_called()
        restarted.workflows.retry(task['id'])
        with patch('studio.adapters.ai_draft', return_value='重启后新待审稿。') as transport:
            restarted.workflows.tick()
            transport.assert_called_once()
            assert task['outline'] in transport.call_args.args[1]
        assert restarted.workflows.get(task['id'])['status'] == 'review'
        assert restarted.post(post['id']) == post
        assert restarted.jobs() == []
    finally:
        restarted.close()


def test_edit_during_skill_body_generation_cannot_be_overwritten(app):
    post, flow = create(app)
    with patch('studio.adapters.ai_draft', return_value='## 章节'):
        app.workflows.tick()
    task = app.workflows.get(flow['id'])
    app.workflows.confirm(task['id'], {'outline': task['outline'], 'outline_version': task['outline_version']})
    def reply(*args, **kwargs):
        app.save_post({'version': post['version'], 'body': '作者在生成时的新修改'}, post['id'])
        return '旧快照生成的待审稿'
    with patch('studio.adapters.ai_draft', side_effect=reply):
        app.workflows.tick()
    with pytest.raises(Problem):
        app.workflows.apply(task['id'], {'version': post['version']})
    assert app.post(post['id'])['body'] == '作者在生成时的新修改'
    assert app.workflows.get(task['id'])['result']['body'] == '旧快照生成的待审稿'
    assert app.jobs() == []


@pytest.mark.parametrize('historical', [False, True])
def test_identical_review_replacement_is_read_only_without_new_revision(app, historical):
    post, _ = create(app)
    review = app.reviews.create({'post_id': post['id'], 'post_version': post['version'], 'request_id': uid()})
    raw = {'summary': '保留意见', 'issues': [{'quote': post['body'], 'replacement': post['body'],
           'reason': '模型没有提供改写', 'category': 'clarity', 'priority': 'info', 'needs_verification': False}]}
    with patch('studio.adapters.ai_draft', return_value=json.dumps(raw)):
        app.reviews.tick()
    if historical:
        old = app.reviews.get(review['id'])['result']
        old['issues'][0]['applicable'] = True
        with app.db.connect(immediate=True) as db:
            db.execute('UPDATE editorial_reviews SET result=? WHERE id=?', (json.dumps(old), review['id']))
    stored = app.db.one('SELECT result FROM editorial_reviews WHERE id=?', (review['id'],))['result']
    revisions = app.db.one('SELECT COUNT(*) n FROM revisions WHERE post_id=?', (post['id'],))['n']
    current = app.reviews.get(review['id'])
    assert current['result']['issues'][0]['applicable'] is False
    assert current['result']['issues'][0]['reason'] == raw['issues'][0]['reason']
    with pytest.raises(Problem):
        app.reviews.apply(review['id'], {'post_version': post['version'], 'issues': [{'id': '0'}]})
    assert app.post(post['id']) == post
    assert app.db.one('SELECT COUNT(*) n FROM revisions WHERE post_id=?', (post['id'],))['n'] == revisions
    assert app.db.one('SELECT result FROM editorial_reviews WHERE id=?', (review['id'],))['result'] == stored
    assert app.reviews.get(review['id'])['status'] == 'review'
