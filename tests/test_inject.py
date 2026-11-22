import sys
from types import SimpleNamespace

import pytest

from spoke import inject
from spoke import text_field


def test_paste_restores_clipboard_and_sends_cmd_v(monkeypatch):
    calls = []
    monkeypatch.setattr(inject, "set_clipboard", lambda text: calls.append(("set", text)))
    monkeypatch.setattr(inject, "press_cmd_v", lambda: calls.append(("paste",)))
    monkeypatch.setattr(inject, "paste_into_application", lambda text, pid, validate, confirm: calls.append(("preserved", text, pid)))
    inject.paste_text("Привет", restore=True, trailing_space=True)
    assert calls == [("preserved", "Привет ", None)]
    calls.clear()
    inject.paste_text("Привет", restore=False)
    assert calls == [("set", "Привет"), ("paste",)]
    calls.clear()
    inject.paste_text("")
    assert calls == []


@pytest.mark.parametrize("copied", [None, "", "Исходный черновик.", "Другой текст."])
def test_browser_confirmation_rejects_an_unverified_replacement(monkeypatch, copied):
    field = text_field.EditableField(None, 1, None, "AXTextArea", "Исходный черновик.", True)
    monkeypatch.setitem(sys.modules, "ApplicationServices", SimpleNamespace(kAXSelectedTextRangeAttribute="AXRange"))
    monkeypatch.setattr(text_field, "_read", lambda *_args: None)
    monkeypatch.setattr(text_field.EditableField, "_check_unchanged", lambda *_args: None)
    monkeypatch.setattr(text_field.EditableField, "_select_web_text", lambda *_args: None)
    monkeypatch.setattr(text_field, "copy_from_application", lambda *_args: copied)

    with pytest.raises(RuntimeError, match="Веб-редактор не подтвердил текст"):
        field._verify_web_text("Новая редакция.")


def test_browser_confirms_model_result_even_when_ax_flattens_paragraphs(monkeypatch):
    field = text_field.EditableField(None, 1, None, "AXTextArea", "черновик", True)
    monkeypatch.setitem(sys.modules, "ApplicationServices", SimpleNamespace(kAXSelectedTextRangeAttribute="AXRange"))
    monkeypatch.setattr(text_field, "_read", lambda *_args: None)
    checks = []
    monkeypatch.setattr(text_field.EditableField, "_check_unchanged", lambda self, value: checks.append(value))
    monkeypatch.setattr(text_field.EditableField, "_select_web_text", lambda *args: None)
    expected = "Первое.\n\nВторое."
    observed = "Первое. Второе."
    monkeypatch.setattr(text_field, "copy_from_application", lambda *args: observed)
    with pytest.raises(RuntimeError):
        field._verify_web_text(expected, observed)
    monkeypatch.setattr(text_field, "copy_from_application", lambda *args: expected)
    field._verify_web_text(expected, observed)
    assert checks == [observed, observed]


def test_partial_browser_paste_cannot_report_success_through_replace(monkeypatch, tmp_path):
    original = "Исходный черновик."
    field = text_field.EditableField(None, 3, None, "AXTextArea", original, True)
    monkeypatch.setattr(text_field, "DATA_DIR", tmp_path)
    monkeypatch.setitem(sys.modules, "ApplicationServices", SimpleNamespace(kAXTextFieldRole="AXTextField", kAXSelectedTextRangeAttribute="range"))
    monkeypatch.setattr(text_field, "_read", lambda *_: None)
    monkeypatch.setattr(text_field.EditableField, "_check_unchanged", lambda *_: None)
    monkeypatch.setattr(text_field.EditableField, "_select_web_text", lambda *_: None)
    monkeypatch.setattr(text_field.EditableField, "_wait_for_change", lambda *_, **kw: "Обрезано")
    monkeypatch.setattr(text_field.EditableField, "_value", lambda _: "Обрезано")
    monkeypatch.setattr(text_field, "paste_into_application", lambda *args: "Обрезано")
    monkeypatch.setattr(text_field, "copy_from_application", lambda *args: "Обрезано")
    with pytest.raises(RuntimeError, match="не подтвердил"):
        field.replace("Полный текст после обработки редактором.")
    assert next((tmp_path / "recovery").glob("*.txt")).read_text() == original
