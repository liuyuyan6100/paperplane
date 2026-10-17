"""Real database integration; mock only the external model."""
from unittest.mock import Mock
import pytest
from studio.service import Studio
from studio.skills import SkillSystem

@pytest.fixture
def app(tmp_path):
    instance = Studio(tmp_path / 'data')
    instance.generate_draft = Mock(return_value='审稿建议')
    yield instance
    instance.close()

def test_review_uses_real_database(app):
    post = app.save_post({'title':'技术文章', 'body':'原稿'})
    original = app.post(post['id'])
    result = SkillSystem(app).execute('review-draft', post=post,
                                      profile=None, model_route={'model':'synthetic'})
    assert result['success'], result['error']
    assert result['data']['suggestions'] == '审稿建议'
    assert result['data']['reviewed_at']
    assert not hasattr(app.db, 'now')
    assert app.post(post['id']) == original
    assert app.jobs() == []

def test_review_error_redacts_provider_payload(app, caplog):
    app.generate_draft.side_effect = RuntimeError('private arbitrary credentials xyz_short')
    result = SkillSystem(app).execute('review-draft', post={'id':'x','title':'标题','body':'正文'},
                                      model_route={'model':'synthetic'})
    assert not result['success']
    assert result['error']['code'] == 'MODEL_ERROR'
    assert 'xyz_short' not in str(result) + caplog.text
