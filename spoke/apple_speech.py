from __future__ import annotations

import ctypes
import json
import os
import platform
import tempfile
import wave

import numpy as np

from spoke.native_bridge import load_library
from spoke.transcribe import SAMPLE_RATE


def available() -> bool:
    version = platform.mac_ver()[0].partition(".")[0]
    return version.isdigit() and int(version) >= 26


class AppleTranscriber:
    def __init__(self):
        self._locale = ""

    def warm_up(self) -> None:
        if not available():
            raise RuntimeError("Диктовка macOS требует macOS 26 или новее")
        self._locale = self._call("spoke_apple_prepare")

    def transcribe(self, audio: np.ndarray, language: str | None, prompt: str | None) -> str:
        if not self._locale:
            self.warm_up()
        with tempfile.NamedTemporaryFile(suffix=".wav") as file:
            with wave.open(file, "wb") as output:
                output.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                output.writeframes((np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes())
            file.flush()
            return self._call("spoke_apple_transcribe", os.fsencode(file.name), self._locale.encode())

    @staticmethod
    def _call(symbol: str, *arguments: bytes) -> str:
        library = load_library()
        native = getattr(library, symbol)
        native.argtypes = [ctypes.c_char_p] * len(arguments)
        native.restype = ctypes.c_void_p
        pointer = native(*arguments)
        if not pointer:
            raise RuntimeError("Распознаватель macOS не вернул результат")
        try:
            answer = json.loads(ctypes.string_at(pointer))
        finally:
            release = library.spoke_apple_free
            release.argtypes = [ctypes.c_void_p]
            release.restype = None
            release(pointer)
        if "error" in answer:
            raise RuntimeError(answer["error"])
        return answer["text"]
