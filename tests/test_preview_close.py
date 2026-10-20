"""Desktop close choices must explain state without trapping a saved article."""

from types import SimpleNamespace

import pytest

from preview import close_message, read_close_state


@pytest.mark.parametrize("value", [None, False, {}, {"dirty": "false"}, {"dirty": 0}])
def test_unreadable_state_is_unknown_not_an_unsaved_article(value):
    window = SimpleNamespace(evaluate_js=lambda _: value)
    assert read_close_state(window) is None
    message = close_message(None)
    assert "不能据此判断文章未保存" in message and "仍然完全退出" in message


def test_bridge_exception_preserves_explicit_exit_and_cancel_choices():
    def fail(_):
        raise RuntimeError("page unavailable")
    assert read_close_state(SimpleNamespace(evaluate_js=fail)) is None
    assert "点“取消”返回检查" in close_message(None)


def test_saved_editor_and_unsaved_creation_have_distinct_messages():
    clean = read_close_state(SimpleNamespace(evaluate_js=lambda _: {"dirty": False, "areas": []}))
    assert clean == {"dirty": False, "areas": []}
    assert "未保存" not in close_message(clean)
    state = read_close_state(SimpleNamespace(evaluate_js=lambda _: {
        "dirty": True, "areas": ["素材与创作：尚未保存的新素材"]
    }))
    message = close_message(state, True)
    assert "尚未保存的新素材" in message and "已保存的文章会保留" in message
    assert "放弃尚未保存的输入并完全退出" in message
    assert "完全退出会中断任务" in message


def test_old_bridge_without_areas_and_malformed_areas_are_safe():
    for value in [{"dirty": True}, {"dirty": True, "areas": "invalid"}]:
        state = read_close_state(SimpleNamespace(evaluate_js=lambda _: value))
        assert "位置未提供" in close_message(state)
