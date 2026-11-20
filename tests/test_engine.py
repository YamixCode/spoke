import json
import time

import pytest

import numpy as np

from spoke import config as cfg, engine as engine_module
from spoke.engine import IDLE, RECORDING, Engine
from spoke.transcribe import SAMPLE_RATE, clean, is_silence


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

    def transcribe(self, audio, language, prompt):
        self.calls.append((len(audio), language, prompt))
        return self.text


def speech(seconds=1.5, level=0.1):
    return (np.random.default_rng(1).standard_normal(int(seconds * SAMPLE_RATE)) * level).astype(np.float32)


def make(audio, text="Привет, мир.", config=None):
    pasted = []
    engine = Engine(config or cfg.Config(sounds=False), recorder=FakeRecorder(audio), transcriber=FakeTranscriber(text),
                    paste=lambda t, restore, space: pasted.append((t, restore, space)))
    return engine, pasted


def test_speech_is_transcribed_and_pasted_with_language_and_vocabulary():
    engine, pasted = make(speech(), config=cfg.Config(sounds=False, vocabulary=["Пётр"], language="ru"))
    assert engine.process(speech()) == "Привет, мир." and pasted == [("Привет, мир.", True, False)]
    assert engine.transcriber.calls == [(int(1.5 * SAMPLE_RATE), "ru", "Термины и имена: Пётр.")]
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
    assert clean("Привет. Субтитры сделал DimaTorzok") == "Привет."
    assert is_silence(np.zeros(100, dtype=np.float32), 0.004) and not is_silence(speech(), 0.004)
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"hotkey": "f99", "language": "xx", "hold_ms": 5, "vocabulary": ["А"], "sounds": False}), encoding="utf-8")
    loaded = cfg.load(path)
    assert (loaded.hotkey, loaded.language, loaded.hold_ms, loaded.vocabulary, loaded.sounds) == ("ctrl", "ru", 100, ["А"], False)
    loaded.language = "auto"
    cfg.save(loaded, path)
    assert cfg.load(path).whisper_language() is None


def test_app_defaults_apply_to_saved_configuration(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkey": "key:0:1048576:⌘A", "language": "ru", "history_size": 3,
                                "auto_space": False, "streaming": False, "autostart": False}), encoding="utf-8")
    loaded = cfg.load(path)
    loaded.apply_app_defaults()
    assert loaded.hotkey_title() == "⌘A" and not loaded.autostart
    assert (loaded.whisper_language(), loaded.history_size, loaded.auto_space, loaded.streaming) == (None, 15, True, True)
    assert cfg.Config().autostart


def test_switching_backends_preserves_the_recording_flow_and_saves_choice(monkeypatch, tmp_path):
    audio = speech()
    engine, pasted = make(audio)
    original = engine.transcriber
    released = []
    original.close = lambda: released.append(True)
    apple = FakeTranscriber("Диктовка macOS.")
    monkeypatch.setattr(engine_module, "make_transcriber", lambda backend, model: apple)
    path = tmp_path / "config.json"
    save = lambda config: cfg.save(config, path)

    deadline = time.monotonic() + 2
    while engine.state != IDLE and time.monotonic() < deadline:
        time.sleep(0.005)
    engine._start()
    engine.switch_backend(cfg.MACOS, save)
    assert engine.transcriber is original and engine.pending_backend is None
    engine._cancel()
    engine.switch_backend(cfg.MACOS, save)
    deadline = time.monotonic() + 2
    while engine.state != IDLE and time.monotonic() < deadline:
        time.sleep(0.005)
    assert engine.transcriber is apple and released == [True]
    assert cfg.load(path).recognition_backend == cfg.MACOS
    assert engine.process(audio) == "Диктовка macOS."
    assert pasted[0][0] == "Диктовка macOS."


def test_failing_backend_switch_keeps_the_working_model(monkeypatch):
    engine, _ = make(speech())
    original = engine.transcriber
    apple = FakeTranscriber()
    saves = []

    def failed():
        raise RuntimeError("Языковая модель недоступна")

    apple.warm_up = failed
    monkeypatch.setattr(engine_module, "make_transcriber", lambda backend, model: apple)
    deadline = time.monotonic() + 2
    while engine.state != IDLE and time.monotonic() < deadline:
        time.sleep(0.005)
    engine.switch_backend(cfg.MACOS, lambda config: saves.append(config))
    deadline = time.monotonic() + 2
    while engine.state != IDLE and time.monotonic() < deadline:
        time.sleep(0.005)
    assert engine.transcriber is original
    assert saves == []
    assert engine.recognition_backend == engine.config.recognition_backend == cfg.WHISPER
    assert engine.pending_backend is None and engine.last_error == "Языковая модель недоступна"


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


def test_key_press_prewarms_idle_model_but_not_while_recording():
    engine, _ = make(speech())
    calls = []
    engine.transcriber.prewarm = lambda *a, **k: calls.append(1)
    engine.state = IDLE
    engine._prewarm()
    engine.state = RECORDING
    engine._prewarm()
    import time as t
    t.sleep(0.2)
    assert calls == [1]


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
    assert cfg.load().language == "auto"
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({"language": "en", "history_size": 3}))
    loaded = cfg.load()
    assert loaded.language == "en" and loaded.history_size == 3
    cfg.save(loaded)
    legacy.unlink()
    assert cfg.load().language == "en" and new.exists()



def test_set_option_parses_values_from_the_settings_window():
    config = cfg.Config()
    for key, text in (("hold_ms", "310"), ("history_size", "7.0"), ("overlay", "false"), ("auto_space", "true"), ("overlay_bottom_margin", "22"),
                      ("hotkey", "right_option"), ("language", "en"), ("vocabulary", " Пётр \n\nKubernetes\n")):
        cfg.set_option(config, key, text)
    assert (config.hold_ms, config.history_size, config.overlay, config.auto_space, config.overlay_bottom_margin) == (310, 7, False, True, 22.0)
    assert (config.hotkey, config.language, config.vocabulary) == ("right_option", "en", ["Пётр", "Kubernetes"])
    for key, text in (("hotkey", "nonsense"), ("language", "xx"), ("unknown", "1")):
        with pytest.raises(KeyError):
            cfg.set_option(config, key, text)
