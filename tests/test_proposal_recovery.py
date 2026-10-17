"""Proposal recovery through real managers, including abrupt isolated child exit."""
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from studio import memories
from studio.adapters import DeliveryError
from studio.service import Studio
from tools.verify_skill_phase4 import KINDS, create_case, run_case, output, assert_success


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERPLANE_USE_SKILLS', '1')
    instance = Studio(tmp_path / 'data')
    instance.config.update({'AI_MODE': 'api', 'AI_BASE_URL': 'https://synthetic.invalid/v1', 'AI_MODEL': 'synthetic'})
    yield instance
    instance.close()


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('retryable,recovers,calls', [(True, True, 2), (True, False, 2), (False, False, 1)])
def test_proposal_retry_budget_and_manual_recovery(app, kind, retryable, recovers, calls):
    case = create_case(app, kind)
    error = DeliveryError('合成临时故障' if retryable else '合成认证失败', retryable=retryable)
    with patch('studio.adapters.ai_draft', side_effect=[error, output(kind) if recovers else error]) as transport:
        if kind == 'memory' and not recovers:
            with pytest.raises(DeliveryError):
                run_case(app, case)
        else:
            result = run_case(app, case)
        assert transport.call_count == calls
    if recovers:
        assert_success(app, case, result)
        return
    if kind == 'memory':
        assert memories.listing(app, case['profile_id']) == []
    else:
        manager = app.topics if kind == 'topic' else app.performance
        assert manager.get(case['id'])['status'] == 'failed'
        with patch('studio.adapters.ai_draft') as transport:
            assert manager.tick() is False
            transport.assert_not_called()
        if kind == 'topic':
            manager.retry(case['id'])
        else:
            manager.retry(case['id'], {})
    with patch('studio.adapters.ai_draft', return_value=output(kind)) as transport:
        assert_success(app, case, run_case(app, case))
        transport.assert_called_once()


CHILD = '''
import json, os, sys
from pathlib import Path
from unittest.mock import patch
from studio.service import Studio
from tools.verify_skill_phase4 import create_case, run_case, output
root, kind = Path(sys.argv[1]), sys.argv[2]
app = Studio(root / 'data')
app.config.update({'AI_MODE':'api','AI_BASE_URL':'https://synthetic.invalid/v1','AI_MODEL':'synthetic'})
done = create_case(app, kind)
with patch('studio.adapters.ai_draft', return_value=output(kind)):
    completed = run_case(app, done)
active = create_case(app, kind)
queued = create_case(app, kind)
(root / 'manifest.json').write_text(json.dumps({'done':done,'completed':completed,'active':active,'queued':queued}), encoding='utf-8')
with patch('studio.adapters.ai_draft', side_effect=lambda *a, **k: os._exit(17)):
    run_case(app, active)
raise AssertionError('Expected abrupt exit')
'''


@pytest.mark.parametrize('kind', KINDS)
def test_abrupt_process_exit_preserves_results_and_requires_manual_retry(tmp_path, kind):
    env = {**os.environ, 'PAPERPLANE_USE_SKILLS': '1'}
    process = subprocess.run([sys.executable, '-c', CHILD, str(tmp_path), kind],
                             cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, timeout=45)
    assert process.returncode == 17, process.stderr.decode(errors='replace')
    manifest = json.loads((tmp_path / 'manifest.json').read_text(encoding='utf-8'))
    app = Studio(tmp_path / 'data')
    try:
        active, done = manifest['active'], manifest['done']
        if kind == 'memory':
            assert memories.listing(app, active['profile_id']) == []
            assert memories.listing(app, manifest['queued']['profile_id']) == []
            assert memories.listing(app, done['profile_id'])[0]['status'] == 'pending'
            assert memories.recall(app, done['profile_id']) == []
        else:
            manager = app.topics if kind == 'topic' else app.performance
            assert manager.get(active['id'])['status'] == 'interrupted'
            assert manager.get(manifest['queued']['id'])['status'] == 'interrupted'
            completed = manager.get(done['id'])
            field = 'candidates' if kind == 'topic' else 'result'
            assert completed[field] == manifest['completed'][field]
            assert completed['status'] == manifest['completed']['status']
            with patch('studio.adapters.ai_draft') as transport:
                assert manager.tick() is False
                transport.assert_not_called()
            if kind == 'topic':
                manager.retry(active['id'])
            else:
                manager.retry(active['id'], {})
        with patch('studio.adapters.ai_draft', return_value=output(kind)) as transport:
            result = run_case(app, active)
            assert_success(app, active, result)
            transport.assert_called_once()
        assert app.post(done['post_id']) == done['post']
    finally:
        app.close()
