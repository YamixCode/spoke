from __future__ import annotations

import json
import os
import platform
import struct
import tempfile

import numpy as np

from spoke.native_bridge import call_json
from spoke.transcribe import SAMPLE_RATE


def available() -> bool:
    version = platform.mac_ver()[0].partition(".")[0]
    return version.isdigit() and int(version) >= 26


class AppleTranscriber:
    def __init__(self, cleanup=None):
        self.cleanup = cleanup
        self._locale = ""

    def warm_up(self) -> None:
        if not available():
            raise RuntimeError("Диктовка macOS требует macOS 26 или новее")
        self._locale = self._call("spoke_apple_prepare")
        if self.cleanup is not None:
            self.cleanup.prepare()

    def transcribe(self, audio: np.ndarray, vocabulary: tuple[str, ...] | list[str] = ()) -> str:
        if not self._locale:
            self.warm_up()
        samples = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
        data_size = samples.nbytes
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + data_size, b"WAVE",
            b"fmt ", 16, 1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16,
            b"data", data_size,
        )
        with tempfile.NamedTemporaryFile(suffix=".wav") as file:
            file.write(header)
            file.write(samples.tobytes())
            file.flush()
            text = self._call("spoke_apple_transcribe", os.fsencode(file.name), self._locale.encode(), json.dumps(vocabulary).encode())
        return self.cleanup.process(text) if self.cleanup is not None else text

    @staticmethod
    def _call(symbol: str, *arguments: bytes) -> str:
        return call_json(symbol, *arguments)["text"]
