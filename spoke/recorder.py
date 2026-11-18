"""Запись с микрофона: 16 кГц, моно, в памяти (на диск ничего не пишется)."""
from __future__ import annotations

import threading

import numpy as np

from spoke.transcribe import SAMPLE_RATE


class Recorder:
    def __init__(self, max_seconds: int = 180):
        self.max_seconds = max_seconds
        self._chunks: list[np.ndarray] = []
        self._stream = None
        self._lock = threading.Lock()
        self.level = 0.0

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        import sounddevice as sd
        with self._lock:
            if self._stream is not None:
                return
            self._chunks = []

            def callback(indata, frames, time_info, status):           # noqa: ARG001
                self.level = float(np.sqrt(np.mean(np.square(indata[:, 0]))))
                if sum(len(c) for c in self._chunks) < self.max_seconds * SAMPLE_RATE:
                    self._chunks.append(indata[:, 0].copy())
            self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback)
            self._stream.start()

    def snapshot(self) -> np.ndarray:
        """Всё записанное к этому моменту (запись продолжается): по нему идёт распознавание кусков на лету."""
        chunks = list(self._chunks)
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)

    def stop(self) -> np.ndarray:
        """Останавливает запись и возвращает звук (пустой массив, если не писали)."""
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        self.level = 0.0
        chunks, self._chunks = self._chunks, []
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)

    def cancel(self) -> None:
        self.stop()
