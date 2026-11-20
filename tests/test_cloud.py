import base64
import json
import threading

import numpy as np
import pytest

from spoke import cloud
from spoke.usage import UsageStore, key_fingerprint


@pytest.fixture(autouse=True)
def isolated_usage(monkeypatch, tmp_path):
    store = UsageStore(tmp_path / "usage.db")
    monkeypatch.setattr(cloud, "usage_store", store)
    return store


def test_scribe_streams_before_stop_and_haiku_formats_once(monkeypatch, isolated_usage):
    sent = threading.Event()
    messages, edits = [], []

    def native(symbol, *args):
        if symbol == "spoke_scribe_open":
            return {"id": "test"}
        if symbol in ("spoke_scribe_send", "spoke_scribe_finish"):
            messages.append(json.loads(args[1]))
            sent.set()
        if symbol == "spoke_scribe_finish":
            return {"text": "Э-э-э, первая мысль. Вторая мысль."}
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
    assert edits == [("первая мысль. Вторая мысль.", "haiku", ("Spoke",))]
    result = isolated_usage.totals({"elevenlabs": key_fingerprint("scribe")}, this_month=True)
    assert result["elevenlabs"]["seconds"] == len(audio) / cloud.SAMPLE_RATE


def test_haiku_failure_preserves_scribe_transcript(monkeypatch):
    raw = "Встреча, эээ, в 15:30."

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
    assert session.finish(audio) == "Встреча, в 15:30."
    assert notes == ["Haiku недоступен. Вставлен результат Scribe."]


@pytest.mark.parametrize(("source", "expected"), [
    ("Эээ. А-а-а, встреча в 15:30.", "встреча в 15:30."),
    ("Надо, эээм, проверить, а потом выпустить.", "Надо, проверить, а потом выпустить."),
    ("Обсудим «эээ» и слово «Аарон», а потом запишем.", "Обсудим «эээ» и слово «Аарон», а потом запишем."),
    ("Э-э-э, ааа!", ""),
])
def test_filler_removal_preserves_words_numbers_and_quotes(source, expected):
    assert cloud._remove_fillers(source) == expected


def test_usage_counts_cumulative_tokens_once_and_separates_keys(monkeypatch, isolated_usage):
    from io import BytesIO

    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 120, "output_tokens": 1}}},
        {"type": "message_delta", "delta": {}, "usage": {"output_tokens": 7}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Текст."}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 16}},
        {"type": "message_stop"},
    ]

    class FakeOpener:
        def open(self, request, timeout):
            return BytesIO(b"".join(b"data: " + json.dumps(event).encode() + b"\n" for event in events))

    monkeypatch.setattr(cloud, "_anthropic_opener", lambda: FakeOpener())
    assert cloud._format_text("Текст.", "active-key", ()) == "Текст."
    isolated_usage.tokens("older-key", 999, 999)
    active = {"anthropic": key_fingerprint("active-key")}
    assert isolated_usage.totals(active, this_month=True)["anthropic"] == {
        "seconds": 0, "input": 120, "output": 16, "usd": 0.000020, "configured": True,
    }
