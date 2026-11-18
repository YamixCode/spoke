"""Распознавание речи: Whisper на Apple Silicon (mlx-whisper), модель держится в памяти, поэтому диктовка отвечает за 1–2 секунды."""
from __future__ import annotations

import re
import threading
import time

import numpy as np

SAMPLE_RATE = 16000
_HALLUCINATIONS = re.compile(r"(?i)(субтитр\w*[^.!?]*[.!?]?|продолжение следует\.*|спасибо за просмотр\w*[.!]?|dimatorzok|thanks for watching[.!]?)")


def clean(text: str) -> str:
    return " ".join(_HALLUCINATIONS.sub(" ", text or "").split())


def is_silence(audio: np.ndarray, rms_threshold: float) -> bool:
    return audio.size == 0 or float(np.sqrt(np.mean(np.square(audio)))) < rms_threshold


class Transcriber:
    def __init__(self, model: str):
        self.model = model
        self._lock = threading.Lock()
        self.ready = False
        self.last_used = time.monotonic()

    def warm_up(self) -> None:
        """Загрузить модель и сделать пробный проход заранее: первая диктовка не ждёт загрузки."""
        self.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), None, None)
        self.ready = True

    def touch(self) -> None:
        """Прочитать все веса модели (доли секунды): macOS не успевает вытеснить их из памяти, пока вы молчите."""
        import mlx.core as mx
        from mlx.utils import tree_flatten
        from mlx_whisper.transcribe import ModelHolder
        model = ModelHolder.model
        if model is None:
            return
        with self._lock:
            mx.eval([weight.max() for _, weight in tree_flatten(model.parameters())])

    def prewarm(self, min_idle: float = 30.0) -> None:
        """Вернуть веса модели в оперативную память после простоя: мелкий пробный проход. Если модель недавно работала, ничего не делает."""
        if self.ready and time.monotonic() - self.last_used >= min_idle:
            self.transcribe(np.zeros(SAMPLE_RATE // 2, dtype=np.float32), None, None)

    def transcribe(self, audio: np.ndarray, language: str | None, prompt: str | None) -> str:
        import mlx_whisper
        with self._lock:
            result = mlx_whisper.transcribe(np.asarray(audio, dtype=np.float32), path_or_hf_repo=self.model, language=language, initial_prompt=prompt,
                                            condition_on_previous_text=False, verbose=None)
        self.last_used = time.monotonic()
        return clean(result.get("text", ""))
