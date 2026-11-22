import sys

import pytest

from spoke import text_field

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="requires AppKit")


def test_native_text_view_accepts_complete_multiline_replacement(monkeypatch):
    from AppKit import NSApplication, NSMakeRect, NSTextView
    import ApplicationServices as ax

    NSApplication.sharedApplication()
    view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 320, 100))
    view.setString_("одна мысль и другая")
    field = text_field.EditableField(None, 1, view, ax.kAXTextAreaRole, str(view.string()))
    monkeypatch.setattr(text_field, "_writable", lambda el, attr: attr == ax.kAXValueAttribute)
    monkeypatch.setattr(text_field, "_read", lambda el, attr: el.accessibilityAttributeValue_(attr))
    monkeypatch.setattr(text_field.EditableField, "_is_focused", lambda self: True)
    def write(el, attr, value):
        el.accessibilitySetValue_forAttribute_(value, attr)
        return ax.kAXErrorSuccess
    monkeypatch.setattr(ax, "AXUIElementSetAttributeValue", write)
    expected = "Одна мысль.\n\nИ другая."
    field._replace_native(expected)
    assert str(view.string()) == expected
