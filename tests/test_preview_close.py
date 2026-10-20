"""Desktop close choices must explain state without trapping a saved article."""

from types import SimpleNamespace

import pytest

from preview import choose_close, close_message, read_close_state
from studio.preview_preferences import PreviewPreferences


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


@pytest.mark.parametrize("choice,expected", [(True, "tray"), (False, "exit")])
def test_first_choice_persists_then_clean_close_does_not_ask(tmp_path, choice, expected):
    settings = PreviewPreferences(tmp_path)
    messages = []
    clean = {"dirty": False, "areas": []}
    assert choose_close(settings, clean, False, lambda text: messages.append(text) or choice) == expected
    assert "会被记住" in messages[0]
    assert PreviewPreferences(tmp_path).get()["close_behavior"] == expected
    def unexpected(_):
        pytest.fail("Saved habit must not ask again")
    assert choose_close(PreviewPreferences(tmp_path), clean, False, unexpected) == expected


def test_cancel_does_not_remember_and_settings_change_is_used_immediately(tmp_path):
    settings = PreviewPreferences(tmp_path)
    clean = {"dirty": False, "areas": []}
    assert choose_close(settings, clean, False, lambda _: None) == "cancel"
    assert settings.get()["close_behavior"] == "ask"
    settings.update("tray")
    assert choose_close(settings, None, True, lambda _: pytest.fail()) == "tray"
    assert choose_close(settings, clean, False, lambda _: pytest.fail(), exit_only=True) == "exit"
    assert settings.get()["close_behavior"] == "tray"


@pytest.mark.parametrize("state,active", [(None, False), ({"dirty": True, "areas": []}, False), ({"dirty": False, "areas": []}, True)])
def test_saved_exit_still_warns_for_unsaved_unknown_or_tasks(tmp_path, state, active):
    settings = PreviewPreferences(tmp_path)
    settings.update("exit")
    called = []
    assert choose_close(settings, state, active, lambda text: called.append(text) or True) == "tray"
    assert called and settings.get()["close_behavior"] == "exit"
