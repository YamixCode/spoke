from spoke import inject


def test_paste_restores_clipboard_and_sends_cmd_v(monkeypatch):
    calls = []
    monkeypatch.setattr(inject, "get_clipboard", lambda: "старый буфер")
    monkeypatch.setattr(inject, "set_clipboard", lambda text: calls.append(("set", text)))
    monkeypatch.setattr(inject, "press_cmd_v", lambda: calls.append(("paste",)))
    monkeypatch.setattr(inject.time, "sleep", lambda s: None)
    scheduled = []
    monkeypatch.setattr(inject.threading, "Timer", lambda delay, fn, args: type("T", (), {"start": lambda self: scheduled.append((delay, fn, args))})())
    inject.paste_text("Привет", restore=True, trailing_space=True)
    assert calls == [("set", "Привет "), ("paste",)]
    assert len(scheduled) == 1 and scheduled[0][0] == 0.3
    scheduled[0][1](*scheduled[0][2])
    assert calls[-1] == ("set", "старый буфер")
    calls.clear()
    inject.paste_text("Привет", restore=False)
    assert calls == [("set", "Привет"), ("paste",)]
    calls.clear()
    inject.paste_text("")
    assert calls == []
