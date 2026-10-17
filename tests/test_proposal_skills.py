"""Exercise the shared proposal boundary without changing persisted business rules."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from studio.adapters import DeliveryError
from studio.model_routing import route_for
from studio.service import Studio
from studio.skills import generate_structured


KINDS = [('plan-topics', 'topic'), ('extract-memories', 'chat'), ('review-performance', 'review')]


@pytest.fixture
def app(tmp_path):
    instance = Studio(tmp_path / 'isolated')
    instance.config.update({'AI_MODE': 'api', 'AI_BASE_URL': 'https://synthetic.invalid/v1',
                            'AI_MODEL': 'synthetic'})
    yield instance
    instance.close()


def draft(app, task):
    return {'title': '合成来源', 'summary': '', 'body': '来源原句，0是零而非缺失。',
            '_model_task': task, '_model_route': route_for(app.config, task),
            '_profile_snapshot': None}


@pytest.mark.parametrize('skill,task', KINDS)
def test_skill_and_compatibility_send_identical_structured_api_payload(app, skill, task):
    post = draft(app, task)
    original = deepcopy(post)
    payloads = []
    system = app.skill_system
    for enabled in (True, False):
        app.skill_system = system if enabled else None
        with patch('studio.adapters.request_json', return_value={'choices': [{'message': {'content': '{"candidates":[]}'}}]}) as model:
            assert generate_structured(app, skill, post, '只根据上述来源提出JSON候选。') == '{"candidates":[]}'
        assert model.call_count == 1
        payloads.append(model.call_args.args[1])
    assert payloads[0] == payloads[1]
    assert post == original
    assert app.jobs() == []


@pytest.mark.parametrize('skill,task', KINDS)
def test_started_skill_failure_never_retries_through_compatibility(app, skill, task):
    with patch.object(app, 'generate_draft', side_effect=DeliveryError('认证失败，请检查模型配置')) as model:
        with pytest.raises(DeliveryError):
            generate_structured(app, skill, draft(app, task), '只提出候选')
    assert model.call_count == 1
    assert app.jobs() == []


@pytest.mark.parametrize('skill,task', KINDS)
def test_mismatched_route_is_rejected_before_any_model_call(app, skill, task):
    post = draft(app, task)
    post['_model_route']['task'] = 'body'
    with patch.object(app, 'generate_draft') as model:
        with pytest.raises(DeliveryError):
            generate_structured(app, skill, post, '只提出候选')
    model.assert_not_called()


@pytest.mark.parametrize('skill,task', KINDS)
def test_unexpected_error_is_sanitized_and_not_retried(app, skill, task):
    with patch.object(app, 'generate_draft', side_effect=RuntimeError('PRIVATE_PAYLOAD_SENTINEL')) as model:
        with pytest.raises(DeliveryError) as caught:
            generate_structured(app, skill, draft(app, task), '只提出候选')
    assert 'PRIVATE_PAYLOAD_SENTINEL' not in str(caught.value)
    assert model.call_count == 1
