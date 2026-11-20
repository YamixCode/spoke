import time

import numpy as np

from spoke import config as cfg
from spoke.engine import Engine
from spoke.history import HistoryStore
from spoke.recorder import Recorder
from spoke.transcribe import SAMPLE_RATE


def speech(seconds=1.5, level=0.1):
    return (np.random.default_rng(1).standard_normal(int(seconds * SAMPLE_RATE)) * level).astype(np.float32)


class FakeRecorder:
    def __init__(self):
        self.discarded = 0

    def discard_journal(self):
        self.discarded += 1


class FakeTranscriber:
    def __init__(self, text="Привет, мир."):
        self.text = text

    def warm_up(self):
        pass

    def transcribe(self, audio, vocabulary):
        return self.text


def engine(tmp_path, paste=lambda *a: None, notify=None, transcriber=None):
    return Engine(cfg.Config(sounds=False), notify=notify or (lambda title, text: None), recorder=FakeRecorder(), transcriber=transcriber or FakeTranscriber(),
                  paste=paste, history_path=tmp_path / "history.jsonl", recovery_file=tmp_path / "recovery.pcm")


def wait_until(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not condition():
        time.sleep(0.02)
    return condition()


def test_text_is_saved_before_paste_so_a_crashing_paste_cannot_lose_it(tmp_path):
    saved = []

    def crashing_paste(text, restore, space):
        saved.append(HistoryStore(tmp_path / "history.jsonl").load())
        raise RuntimeError("падение вставки")
    e = engine(tmp_path, paste=crashing_paste)
    e.process(speech())
    assert saved == [["Привет, мир."]]
    assert HistoryStore(tmp_path / "history.jsonl").load() == ["Привет, мир."]
    assert e.recorder.discarded == 0


def test_journal_is_discarded_only_after_successful_paste(tmp_path):
    e = engine(tmp_path)
    e.process(speech())
    assert e.recorder.discarded == 1


def test_history_survives_restart_and_can_be_cleared(tmp_path):
    first = engine(tmp_path)
    first.process(speech())
    first.transcriber.text = "Второе."
    first.process(speech())
    second = engine(tmp_path)
    assert list(second.history) == ["Второе.", "Привет, мир."]
    second.clear_history()
    assert list(engine(tmp_path).history) == [] and not (tmp_path / "history.jsonl").exists()


def test_history_is_not_written_when_disabled(tmp_path):
    e = Engine(cfg.Config(sounds=False, save_history=False), recorder=FakeRecorder(), transcriber=FakeTranscriber(), paste=lambda *a: None,
               history_path=tmp_path / "history.jsonl")
    e.process(speech())
    assert list(e.history) == ["Привет, мир."] and not (tmp_path / "history.jsonl").exists()


def test_unfinished_recording_is_recovered_after_crash(tmp_path):
    pending = tmp_path / "recovery.pcm"
    (np.clip(speech(2.0), -1, 1) * 32767).astype("<i2").tofile(pending)
    notes = []
    e = engine(tmp_path, notify=lambda title, text: notes.append(text))
    assert wait_until(lambda: not pending.exists() and notes)
    assert list(e.history) == ["Привет, мир."]
    assert HistoryStore(tmp_path / "history.jsonl").load() == ["Привет, мир."]
    assert "Восстановлена" in notes[0]


def test_recovery_keeps_file_when_transcription_fails(tmp_path):
    class Failing(FakeTranscriber):
        calls = 0

        def transcribe(self, audio, vocabulary):
            Failing.calls += 1
            raise RuntimeError("модель")
    pending = tmp_path / "recovery.pcm"
    (np.clip(speech(2.0), -1, 1) * 32767).astype("<i2").tofile(pending)
    e = engine(tmp_path, transcriber=Failing())
    assert wait_until(lambda: Failing.calls >= 1)
    time.sleep(0.2)
    assert pending.exists() and list(e.history) == []


def test_recorder_journal_holds_audio_written_while_recording(tmp_path):
    path = tmp_path / "rec" / "recording.pcm"
    recorder = Recorder(journal=path)
    recorder._open_journal()
    chunks = [speech(0.5), speech(0.5, 0.05)]
    for chunk in chunks:
        recorder._journal_queue.put(chunk)
    recorder._close_journal()
    stored = np.fromfile(path, dtype="<i2").astype(np.float32) / 32767
    assert len(stored) == sum(len(chunk) for chunk in chunks) and np.allclose(stored, np.concatenate(chunks), atol=1e-3)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    recorder.discard_journal()
    assert not path.exists()


def test_history_store_orders_newest_first_trims_and_skips_garbage(tmp_path):
    store = HistoryStore(tmp_path / "h.jsonl", limit=3)
    for text in ("a", "b", "c", "d"):
        store.add(text)
    (tmp_path / "h.jsonl").write_text((tmp_path / "h.jsonl").read_text() + "не json\n")
    assert store.load() == ["d", "c", "b"]
    for index in range(20):
        store.add(f"x{index}")
    assert len((tmp_path / "h.jsonl").read_text().splitlines()) <= 13
    assert HistoryStore(tmp_path / "missing.jsonl").load() == []


def test_no_pynput_anywhere_in_the_application():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = [str(path) for path in (root / "spoke").glob("*.py") if "import pynput" in path.read_text() or "from pynput" in path.read_text()]
    assert offenders == []
