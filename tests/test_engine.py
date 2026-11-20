import json
import time

import pytest

import numpy as np

from spoke import config as cfg
from spoke.engine import IDLE, RECORDING, Engine
from spoke.transcribe import SAMPLE_RATE, is_silence


class FakeRecorder:
    def __init__(self, audio):
        self.audio, self.started, self.cancelled = audio, 0, 0

    def start(self):
        self.started += 1

    def stop(self):
        return self.audio

    def cancel(self):
        self.cancelled += 1


class FakeTranscriber:
    def __init__(self, text="Привет, мир."):
        self.text, self.calls = text, []

    def warm_up(self):
        pass

    def transcribe(self, audio, vocabulary):
        self.calls.append((len(audio), vocabulary))
        return self.text


def speech(seconds=1.5, level=0.1):
    return (np.random.default_rng(1).standard_normal(int(seconds * SAMPLE_RATE)) * level).astype(np.float32)


def make(audio, text="Привет, мир.", config=None):
    pasted = []
    engine = Engine(config or cfg.Config(sounds=False), recorder=FakeRecorder(audio), transcriber=FakeTranscriber(text),
                    paste=lambda t, restore, space: pasted.append((t, restore, space)))
    return engine, pasted


def test_speech_is_transcribed_and_pasted_with_vocabulary():
    engine, pasted = make(speech(), config=cfg.Config(sounds=False, vocabulary=["Пётр"]))
    assert engine.process(speech()) == "Привет, мир." and pasted == [("Привет, мир.", True, False)]
    assert engine.transcriber.calls == [(int(1.5 * SAMPLE_RATE), ["Пётр"])]
    assert engine.state == IDLE and engine.last_text == "Привет, мир."


def test_short_taps_and_silence_are_ignored():
    engine, pasted = make(None)
    assert engine.process(speech(0.2)) == "" and engine.process(np.zeros(2 * SAMPLE_RATE, dtype=np.float32)) == ""
    assert pasted == [] and engine.transcriber.calls == []


def test_errors_do_not_kill_the_engine_and_are_reported():
    notes = []
    engine, _ = make(None)
    engine.notify = lambda title, text: notes.append(text)
    engine.paste = lambda *a: (_ for _ in ()).throw(RuntimeError("нет доступа"))
    assert engine.process(speech()) == "" and engine.last_error == "нет доступа" and notes and engine.state == IDLE
    engine.paste = lambda *a: None
    assert engine.process(speech()) == "Привет, мир." and engine.last_error == ""


def test_start_stop_cancel_flow_through_the_recorder():
    engine, _ = make(speech())
    engine.state = IDLE
    engine._start()
    assert engine.state == RECORDING and engine.recorder.started == 1
    engine._cancel()
    assert engine.state == IDLE and engine.recorder.cancelled == 1
    engine._stop()
    assert engine.state == IDLE


def test_filters_and_config(tmp_path):
    assert is_silence(np.zeros(100, dtype=np.float32), 0.004) and not is_silence(speech(), 0.004)
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"hotkey": "f99", "recognition_backend": "whisper", "model": "old-weights",
                                "keep_warm_seconds": 45, "hold_ms": 5, "vocabulary": ["А"], "sounds": False}), encoding="utf-8")
    loaded = cfg.load(path)
    assert (loaded.hotkey, loaded.hold_ms, loaded.vocabulary, loaded.sounds) == ("ctrl", 100, ["А"], False)
    cfg.save(loaded, path)
    assert not {"model", "recognition_backend", "keep_warm_seconds"}.intersection(json.loads(path.read_text()))



def test_app_defaults_apply_to_saved_configuration(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkey": "key:0:1048576:⌘A", "history_size": 3,
                                "auto_space": False, "streaming": False, "autostart": False}), encoding="utf-8")
    loaded = cfg.load(path)
    loaded.apply_app_defaults()
    assert loaded.hotkey_title() == "⌘A" and not loaded.autostart
    assert (loaded.history_size, loaded.auto_space, loaded.streaming) == (15, True, True)
    assert cfg.Config().autostart


def test_engine_uses_apple_with_cleanup_even_if_legacy_config_selected_whisper(tmp_path, monkeypatch):
    from spoke import engine as module

    config_file = tmp_path / "config.json"
    config_file.write_text('{"recognition_backend": "whisper"}')
    transcriber = FakeTranscriber("Диктовка macOS.")
    cleanups = []
    monkeypatch.setattr(module, "AppleTranscriber", lambda cleanup: cleanups.append(cleanup) or transcriber)
    pasted = []
    engine = Engine(cfg.load(config_file), recorder=FakeRecorder(speech()), paste=lambda t, r, s: pasted.append(t))
    assert engine.transcriber is transcriber and cleanups == [engine.cleanup]
    assert engine.process(speech()) == "Диктовка macOS."
    assert pasted == ["Диктовка macOS."]


def test_consecutive_pastes_are_separated_by_space_but_first_is_not():
    engine, pasted = make(speech(), text="Первая фраза.")
    engine.process(speech())
    engine.process(speech())
    assert [p[0] for p in pasted] == ["Первая фраза.", " Первая фраза."]


def test_no_space_after_long_pause_or_when_disabled():
    engine, pasted = make(speech(), text="Текст.", config=cfg.Config(sounds=False, join_window_seconds=0.0))
    engine.process(speech())
    engine.process(speech())
    assert [p[0] for p in pasted] == ["Текст.", "Текст."]
    engine, pasted = make(speech(), text="Текст.", config=cfg.Config(sounds=False, auto_space=False))
    engine.process(speech())
    engine.process(speech())
    assert [p[0] for p in pasted] == ["Текст.", "Текст."]


def test_locked_recording_is_finished_or_cancelled_from_overlay_buttons():
    engine, pasted = make(speech())
    engine.state = RECORDING
    engine.gate.state = "locked"
    engine.finish_locked()
    assert engine.state == "working" and engine.gate.state == "idle"
    engine.state = RECORDING
    engine.gate.state = "locked"
    engine.cancel_locked()
    assert engine.state == IDLE and engine.recorder.cancelled == 1 and engine.gate.state == "idle"
    engine.state = RECORDING
    engine.finish_locked()
    assert engine.state == RECORDING


def test_history_keeps_recent_dictations_and_pastes_chosen_one(monkeypatch):
    engine, pasted = make(speech(), text="Раз.", config=cfg.Config(sounds=False, history_size=2))
    engine.process(speech())
    engine.transcriber.text = "Два."
    engine.process(speech())
    engine.transcriber.text = "Три."
    engine.process(speech())
    assert list(engine.history) == ["Три.", "Два."]
    import time as t
    pasted.clear()
    assert engine.paste_from_history(1) and not engine.paste_from_history(5)
    t.sleep(0.5)
    assert pasted == [("Два.", True, False)]


def test_default_config_path_loads_settings_and_migrates_legacy_file(tmp_path, monkeypatch):
    new, legacy = tmp_path / "spoke" / "config.json", tmp_path / "localflow" / "config.json"
    monkeypatch.setattr(cfg, "CONFIG_PATH", new)
    monkeypatch.setattr(cfg, "LEGACY_CONFIG_PATH", legacy)
    assert cfg.load().history_size == 15
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({"hotkey": "right_option", "history_size": 3}))
    loaded = cfg.load()
    assert loaded.hotkey == "right_option" and loaded.history_size == 3
    cfg.save(loaded)
    legacy.unlink()
    assert cfg.load().hotkey == "right_option" and new.exists()



def test_set_option_parses_values_from_the_settings_window():
    config = cfg.Config()
    for key, text in (("hold_ms", "310"), ("history_size", "7.0"), ("overlay", "false"), ("auto_space", "true"), ("overlay_bottom_margin", "22"),
                      ("hotkey", "right_option"), ("vocabulary", " Пётр \n\nKubernetes\n")):
        cfg.set_option(config, key, text)
    assert (config.hold_ms, config.history_size, config.overlay, config.auto_space, config.overlay_bottom_margin) == (310, 7, False, True, 22.0)
    assert (config.hotkey, config.vocabulary) == ("right_option", ["Пётр", "Kubernetes"])
    for key, text in (("hotkey", "nonsense"), ("unknown", "1")):
        with pytest.raises(KeyError):
            cfg.set_option(config, key, text)
