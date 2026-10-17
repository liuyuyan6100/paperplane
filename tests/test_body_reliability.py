"""Verify assembled requests, legacy parity and preservation of unresolved evidence."""
from unittest.mock import patch

import pytest

from studio.service import Studio
from studio.model_routing import route_for
from studio.writing_rules import BODY_RELIABILITY


@pytest.fixture
def app(tmp_path):
    instance = Studio(tmp_path / 'isolated')
    instance.config.update({'AI_MODE':'api','AI_MODEL':'synthetic','AI_BASE_URL':'https://synthetic.invalid/v1'})
    yield instance
    instance.close()


@pytest.mark.parametrize('skills', ['1', '0'])
def test_outline_keeps_notes_body_changes_instructions_but_never_strips_output(app, monkeypatch, skills):
    monkeypatch.setenv('PAPERPLANE_USE_SKILLS',skills)
    if skills == '0': app.skill_system = None
    post = app.save_post({'title':'验证恢复','body':'原稿'})
    task = app.workflows.create({'post_id':post['id'],'version':1})
    outline = '## 验证范围\n- 散列一致只支持字节一致。\n- [待核实]完整恢复方法'
    reply = lambda body: {'choices':[{'message':{'content':body}}]}
    with patch('studio.adapters.request_json',return_value=reply(outline)) as transport:
        app.workflows.tick()
    outline_payload = transport.call_args.args[1]
    assert '缺少依据用[待核实]标明' in outline_payload['messages'][0]['content']
    task = app.workflows.get(task['id'])
    app.workflows.confirm(task['id'],{'outline':task['outline'],'outline_version':task['outline_version']})
    raw = '散列一致不代表所有功能可恢复。[待核实]关键依据'
    with patch('studio.adapters.request_json',return_value=reply(raw)) as transport:
        app.workflows.tick()
    payload=transport.call_args.args[1]
    assert BODY_RELIABILITY in payload['messages'][0]['content']
    assert BODY_RELIABILITY in payload['messages'][1]['content']
    assert outline in payload['messages'][1]['content']  # Evidence was not removed from input.
    assert '缺少依据明确写[待核实]' not in payload['messages'][1]['content']
    assert '缺少依据标为待核实' not in payload['messages'][1]['content']
    finished=app.workflows.get(task['id'])
    assert finished['result']['body'] == raw
    assert any('正文含待核实' in w for w in finished['result']['review']['warnings'])
    assert app.post(post['id'])['body']=='原稿'
    assert app.jobs()==[]


@pytest.mark.parametrize('style',['main','creative','professional'])
def test_all_expansion_styles_reach_api_with_body_rules(app,style):
    post=app.save_post({'title':'测试正文'})
    with patch('studio.adapters.request_json',return_value={'choices':[{'message':{'content':'仍需验证的结论。'}}]}) as transport:
        result=app.skill_system.execute('expand-section',post=post,materials=[],model_route=route_for(app.config,'body'),
                  outline='## 结论边界',confirmed=True,style=style)
    assert result['success']
    payload=transport.call_args.args[1]
    assert BODY_RELIABILITY in payload['messages'][0]['content']
    assert '缺少依据标为待核实' not in payload['messages'][1]['content']
    assert '缺少核心依据时明确该结论尚无法确定' in payload['messages'][1]['content']


def test_program_body_and_outline_use_same_phase_boundary(app):
    app.config.update({'AI_MODE':'program','AI_PROGRAM':'synthetic'})
    post=app.save_post({'title':'阶段检查'})
    with patch('studio.local_ai.run',return_value='未验证结论[待核实]') as transport:
        result=app.generate_draft({**post,'_writing_phase':'body'},'生成正文')
    assert BODY_RELIABILITY in transport.call_args.args[0]
    assert result=='未验证结论[待核实]'
    with patch('studio.local_ai.run',return_value='提纲') as transport:
        app.generate_draft({**post,'_writing_phase':'outline'},'生成提纲')
    assert '缺少依据用[待核实]标明' in transport.call_args.args[0]
    assert BODY_RELIABILITY not in transport.call_args.args[0]
