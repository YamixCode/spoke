import sys
from types import SimpleNamespace

import numpy as np
import pytest

from spoke import config, inject, recorder, terminal, updater


@pytest.mark.parametrize("raw", ["[]", "null", '{"vocabulary":[123],"max_seconds":true}'])
def test_invalid_config_values_and_atomic_save(tmp_path, raw):
    path = tmp_path / "config.json"
    path.write_text(raw)
    assert config.load(path) == config.Config()
    config.save(config.Config(hotkey="right_ctrl"), path)
    assert config.load(path).hotkey == "right_ctrl"
    assert path.stat().st_mode & 0o777 == 0o600


def test_failed_mic_start_keeps_previous_recovery_and_closes_new_journal(tmp_path, monkeypatch):
    path = tmp_path / "recording.pcm"
    path.write_bytes(b"old audio")
    closed = []
    stream = SimpleNamespace(start=lambda: (_ for _ in ()).throw(OSError("no mic")), close=lambda: closed.append(True))
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(InputStream=lambda **kwargs: stream))
    mic = recorder.Recorder(journal=path)
    with pytest.raises(OSError):
        mic.start()
    assert closed and not mic.recording and mic._journal_writer is None
    assert list(tmp_path.iterdir()) == [path] and path.read_bytes() == b"old audio"


def test_recording_limit_signals_once_and_keeps_only_allowed_samples(monkeypatch):
    callbacks, reached = [], []
    def open_stream(**kwargs):
        callbacks.append(kwargs["callback"])
        return SimpleNamespace(start=lambda: None, stop=lambda: None, close=lambda: None)
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(InputStream=open_stream))
    mic = recorder.Recorder(max_seconds=1)
    mic.on_limit = lambda: reached.append(1)
    mic.start()
    frame = np.ones((10_000, 1), dtype=np.float32)
    for _ in range(4):
        callbacks[0](frame, len(frame), None, None)
    assert reached == [1] and len(mic.stop()) == recorder.SAMPLE_RATE


def test_clipboard_restore_preserves_new_owner():
    board = SimpleNamespace(changeCount=lambda: 3)
    inject._restore_clipboard(board, 2, ["previous"])


def test_failed_activation_restores_old_install(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    (code / "version").write_text("old")
    with pytest.raises(OSError):
        updater.activate(tmp_path / "missing", code)
    assert (code / "version").read_text() == "old"


@pytest.mark.parametrize(("phase", "process", "available"), [
    ("prompt", "zsh", True), ("busy", "zsh", False), ("busy", "vim", False),
    ("busy", "claude", True), ("busy", "codex", True),
])
def test_terminal_only_uses_registered_interactive_editors(tmp_path, monkeypatch, phase, process, available):
    surface = "panel"
    (tmp_path / "panel.tty").write_text(f"5 ttys001 {phase}")
    group = 5 if process == "zsh" else 8
    rows = f"5 1 5 {group} /bin/zsh\n"
    if group == 8:
        rows += f"8 5 8 8 /bin/{process}\n"
    monkeypatch.setattr(terminal, "ROOT", tmp_path)
    monkeypatch.setattr(terminal.subprocess, "run", lambda *_a, **_kw: SimpleNamespace(stdout=rows))
    if available:
        assert terminal._editor_kind(surface) == ("zsh" if process == "zsh" else "editor")
    else:
        with pytest.raises(RuntimeError):
            terminal._editor_kind(surface)


def test_terminal_external_editor_round_trip_and_focus_change(tmp_path, monkeypatch):
    import threading
    from spoke import terminal_editor

    target = ("test-workspace", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setattr(terminal, "focused_surface", lambda: target)
    monkeypatch.setattr(terminal, "_editor_kind", lambda _: "editor")
    monkeypatch.setenv("CMUX_SURFACE_ID", target[1])
    draft = tmp_path / "draft"
    draft.write_text("идея один\nидея два")
    helpers = []
    def open_editor(*args):
        assert args == ("send-key", "--workspace", target[0], "--surface", target[1], "ctrl+g")
        def invoke():
            try:
                terminal_editor.main([str(draft)])
            except ValueError:
                pass
        task = threading.Thread(target=invoke)
        task.start()
        helpers.append(task)
    monkeypatch.setattr(terminal, "_cmux", open_editor)
    focused = True
    field = terminal.TerminalField.capture(lambda: focused)
    try:
        assert field.text == draft.read_text()
        assert field.replace("Первая идея.\n\nВторая идея.")
        assert len(helpers) == 2
    finally:
        field.close()
    field = terminal.TerminalField.capture(lambda: focused)
    try:
        focused = False
        with pytest.raises(RuntimeError, match="Вкладка"):
            field.replace("Другое окно")
    finally:
        field.close()
    for task in helpers:
        task.join(timeout=2)
        assert not task.is_alive()
    assert draft.read_text() == "Первая идея.\n\nВторая идея."


def test_cleanup_unloads_only_if_cloud_mode_is_still_selected(monkeypatch):
    from spoke import cleanup

    model = cleanup.Cleanup()
    model._encoding = object()
    released = []
    monkeypatch.setattr(cleanup, "call_json", lambda symbol: released.append(symbol))
    model.release_if_disabled()
    assert model._encoding is not None and not released
    model.enabled = False
    model.release_if_disabled()
    model.release_if_disabled()
    assert model._encoding is None and released == ["spoke_cleanup_release"]
