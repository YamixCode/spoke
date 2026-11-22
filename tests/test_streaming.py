import time

import numpy as np

from spoke import config as cfg
from spoke.engine import IDLE, Engine
from spoke.streaming import StreamSession, find_cut
from spoke.transcribe import SAMPLE_RATE

RNG = np.random.default_rng(7)


def talk(seconds, level=0.1):
    return (RNG.standard_normal(int(seconds * SAMPLE_RATE)) * level).astype(np.float32)


def pause(seconds):
    return (RNG.standard_normal(int(seconds * SAMPLE_RATE)) * 0.0004).astype(np.float32)


def test_find_cut_uses_the_first_pause_after_the_minimum_and_never_a_too_early_or_missing_one():
    audio = np.concatenate([talk(2), pause(0.6), talk(3), pause(0.6), talk(2)])
    cut = find_cut(audio, 4.0, 14.0, 0.45, 0.004)
    assert cut is not None and 5.6 * SAMPLE_RATE < cut < 6.2 * SAMPLE_RATE
    comma = np.concatenate([talk(5), pause(0.35), talk(2)])
    assert find_cut(comma, 3.0, 14.0, 0.6, 0.004) is None
    late = np.concatenate([talk(9), pause(0.35), talk(2)])
    assert find_cut(late, 3.0, 14.0, 0.6, 0.004) is not None
    assert find_cut(np.concatenate([talk(3), pause(0.6), talk(0.5)]), 4.0, 14.0, 0.45, 0.004) is None
    assert find_cut(talk(8), 4.0, 14.0, 0.45, 0.004) is None
    assert find_cut(pause(10), 4.0, 14.0, 0.45, 0.004) is None
    assert find_cut(np.concatenate([talk(6), pause(0.3), talk(8)]), 4.0, 14.0, 0.45, 0.004) is not None


class GrowingRecorder:
    """Звук «записывается» кусками: snapshot отдаёт то, что набралось."""

    def __init__(self, full):
        self.full, self.upto = full, 0

    def advance(self, seconds):
        self.upto = min(len(self.full), self.upto + int(seconds * SAMPLE_RATE))

    def snapshot_from(self, offset):
        return self.full[offset:self.upto]


class ScriptedTranscriber:
    def __init__(self, texts):
        self.texts, self.calls = list(texts), []

    def warm_up(self):
        pass

    def transcribe(self, audio, vocabulary):
        self.calls.append((len(audio) / SAMPLE_RATE, vocabulary))
        return self.texts.pop(0)


def test_finished_chunks_are_transcribed_while_speaking_and_finish_only_does_the_tail():
    full = np.concatenate([talk(5), pause(0.6), talk(5), pause(0.6), talk(2)])
    recorder = GrowingRecorder(full)
    transcriber = ScriptedTranscriber(["Первая часть.", "Вторая часть.", "Хвост."])
    session = StreamSession(recorder, transcriber, ("Пётр",), poll=0.02).start()
    for _ in range(14):
        recorder.advance(1)
        time.sleep(0.15)
    assert len(transcriber.calls) == 2
    assert transcriber.calls[0][1] == ("Пётр",)
    assert transcriber.calls[1][1] == ("Пётр",)
    started = time.monotonic()
    text = session.finish(full)
    assert text == "Первая часть. Вторая часть. Хвост." and len(transcriber.calls) == 3
    assert transcriber.calls[2][0] < 2.6
    assert time.monotonic() - started < 1.0


def test_short_dictation_without_pauses_is_a_single_transcription_and_engine_wires_the_session():
    full = talk(2.5)
    recorder = GrowingRecorder(full)
    recorder.upto = len(full)
    pasted, transcriber = [], ScriptedTranscriber(["Привет, мир."])
    engine = Engine(cfg.Config(sounds=False), recorder=type("R", (), {"start": lambda s: None, "stop": lambda s: full, "cancel": lambda s: None,
                                                                       "snapshot_from": lambda s, offset: full[offset:]})(),
                    transcriber=transcriber, paste=lambda t, r, sp: pasted.append(t))
    deadline = time.time() + 3
    while engine.state != IDLE and time.time() < deadline:
        time.sleep(0.02)
    engine._start()
    time.sleep(0.1)
    assert engine._session is not None
    engine._stop()
    deadline = time.time() + 3
    while not pasted and time.time() < deadline:
        time.sleep(0.02)
    assert pasted == ["Привет, мир."] and len(transcriber.calls) == 1


def test_cancel_stops_the_background_thread():
    session = StreamSession(GrowingRecorder(talk(1)), ScriptedTranscriber([]), poll=0.02).start()
    session.cancel()
    session._thread.join(timeout=1)
    assert not session._thread.is_alive()
