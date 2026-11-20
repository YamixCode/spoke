import base64
import json
import threading

import numpy as np

from spoke import cloud


def test_scribe_streams_before_stop_and_haiku_formats_once(monkeypatch):
    sent = threading.Event()
    messages, edits = [], []

    def native(symbol, *args):
        if symbol == "spoke_scribe_open":
            return {"id": "test"}
        if symbol in ("spoke_scribe_send", "spoke_scribe_finish"):
            messages.append(json.loads(args[1]))
            sent.set()
        if symbol == "spoke_scribe_finish":
            return {"text": "Первая мысль. Вторая мысль."}
        return {}

    def polish(raw, key, vocabulary):
        edits.append((raw, key, vocabulary))
        return "Первая мысль.\n\nВторая мысль."

    monkeypatch.setattr(cloud, "_native", native)
    monkeypatch.setattr(cloud, "_format_text", polish)
    audio = np.full(cloud.CHUNK + 7, 0.1, dtype=np.float32)
    session = cloud.CloudSession(cloud.CloudKeys("scribe", "haiku"), ["Spoke"], lambda offset: audio[offset:],
                                 lambda *args: None).start()
    assert sent.wait(1)
    assert not edits
    assert session.finish(audio) == "Первая мысль.\n\nВторая мысль."
    assert [message["commit"] for message in messages] == [False, True]
    assert [len(base64.b64decode(message["audio_base_64"])) for message in messages] == [cloud.CHUNK * 2, 14]
    assert edits == [("Первая мысль. Вторая мысль.", "haiku", ("Spoke",))]


def test_haiku_failure_preserves_scribe_transcript(monkeypatch):
    raw = "Встреча в 15:30."

    def native(symbol, *args):
        return {"id": "test"} if symbol == "spoke_scribe_open" else {"text": raw}

    def network_error(*args):
        raise TimeoutError()

    monkeypatch.setattr(cloud, "_native", native)
    monkeypatch.setattr(cloud, "_format_text", network_error)
    notes = []
    audio = np.full(cloud.CHUNK, 0.1, dtype=np.float32)
    session = cloud.CloudSession(cloud.CloudKeys("scribe", "haiku"), (), lambda offset: audio[offset:],
                                 lambda title, text: notes.append(text)).start()
    assert session.finish(audio) == raw
    assert notes == ["Haiku недоступен. Вставлен результат Scribe."]
