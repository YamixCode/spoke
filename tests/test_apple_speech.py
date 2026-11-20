import os
import wave
from pathlib import Path

import numpy as np

from spoke.apple_speech import AppleTranscriber
from spoke.transcribe import SAMPLE_RATE


def test_apple_transcriber_passes_a_valid_pcm_file_to_macos(monkeypatch):
    paths = []

    def native(symbol, filename, locale):
        assert symbol == "spoke_apple_transcribe" and locale == b"ru_RU"
        paths.append(Path(os.fsdecode(filename)))
        with wave.open(str(paths[-1]), "rb") as wav:
            assert wav.getparams()[:4] == (1, 2, SAMPLE_RATE, 3)
            assert np.frombuffer(wav.readframes(3), "<i2").tolist() == [-32767, 0, 32767]
        return "Тест."

    transcriber = AppleTranscriber()
    transcriber._locale = "ru_RU"
    monkeypatch.setattr(transcriber, "_call", native)
    assert transcriber.transcribe(np.array([-2, 0, 2], dtype=np.float32), None, None) == "Тест."
    assert len(paths) == 1 and not paths[0].exists()
