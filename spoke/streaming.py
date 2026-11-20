"""Распознаёт готовые фрагменты в фоне и обрабатывает хвост по окончании записи."""
from __future__ import annotations

import threading

import numpy as np

from spoke.transcribe import SAMPLE_RATE

FRAME = int(0.02 * SAMPLE_RATE)
GUARD = int(0.15 * SAMPLE_RATE)


def _frame_rms(audio: np.ndarray) -> np.ndarray:
    n = len(audio) // FRAME
    if n == 0:
        return np.zeros(0)
    return np.sqrt(np.mean(np.square(audio[: n * FRAME].reshape(n, FRAME)), axis=1))


SOFT_AFTER = 8.0
SHORT_PAUSE = 0.3


def find_cut(audio: np.ndarray, min_chunk: float, max_chunk: float, min_pause: float, silence_rms: float) -> int | None:
    """Индекс отсчёта, где можно закончить кусок, или None (ждём дальше). Режет по первой длинной паузе (`min_pause`, обычно конец предложения)
    после `min_chunk`; если кусок уже длиннее 8 секунд, годится и короткая пауза; если пауз нет до `max_chunk`, то в самом тихом месте.
    Так предложение реже разрывается посередине."""
    rms = _frame_rms(audio)
    if len(rms) * FRAME < min_chunk * SAMPLE_RATE + GUARD:
        return None
    speech = float(np.percentile(rms, 90))
    if speech < silence_rms:
        return None
    quiet = rms < max(silence_rms, speech * 0.12)
    first = int(min_chunk / 0.02)
    last_allowed = (len(audio) - GUARD) // FRAME
    run_start = None
    for i in range(first, last_allowed):
        if quiet[i]:
            run_start = i if run_start is None else run_start
            length = i - run_start + 1
            need = int((SHORT_PAUSE if i * FRAME >= SOFT_AFTER * SAMPLE_RATE else min_pause) / 0.02)
            if length >= need:
                return (run_start + length // 2) * FRAME
        else:
            run_start = None
    if len(audio) >= max_chunk * SAMPLE_RATE:
        lo, hi = first, min(last_allowed, int(max_chunk / 0.02))
        window = 10
        if hi - lo > window:
            sums = np.convolve(rms[lo:hi], np.ones(window), "valid")
            return (lo + int(np.argmin(sums)) + window // 2) * FRAME
    return None


class StreamSession:
    """Одна диктовка: фоновый поток режет и распознаёт готовые куски, `finish` распознаёт хвост и склеивает текст."""

    def __init__(self, recorder, transcriber, vocabulary: tuple[str, ...] = (), min_chunk=3.0, max_chunk=14.0, min_pause=0.45,
                 silence_rms=0.004, poll=0.25):
        self.recorder, self.transcriber, self.vocabulary = recorder, transcriber, vocabulary
        self.min_chunk, self.max_chunk, self.min_pause, self.silence_rms, self.poll = min_chunk, max_chunk, min_pause, silence_rms, poll
        self.parts: list[str] = []
        self.cut = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._lock = threading.Lock()

    def start(self) -> "StreamSession":
        self._thread.start()
        return self

    def _transcribe(self, audio: np.ndarray) -> str:
        return self.transcriber.transcribe(audio, self.vocabulary)

    def _loop(self) -> None:
        while not self._stop.wait(self.poll):
            try:
                audio = self.recorder.snapshot()
                pending = audio[self.cut:]
                point = find_cut(pending, self.min_chunk, self.max_chunk, self.min_pause, self.silence_rms)
                if point is None:
                    continue
                with self._lock:
                    chunk = pending[:point]
                    if np.sqrt(np.mean(np.square(chunk))) >= self.silence_rms:
                        text = self._transcribe(chunk)
                        if text:
                            self.parts.append(text)
                    self.cut += point
            except Exception:
                return

    def finish(self, audio: np.ndarray) -> str:
        """Звук целиком после остановки записи: распознать всё, что ещё не распознано, и склеить."""
        self._stop.set()
        self._thread.join(timeout=30)
        with self._lock:
            tail = audio[self.cut:]
            if len(tail) >= int(0.3 * SAMPLE_RATE) and np.sqrt(np.mean(np.square(tail))) >= self.silence_rms:
                text = self._transcribe(tail)
                if text:
                    self.parts.append(text)
            return " ".join(self.parts).strip()

    def cancel(self) -> None:
        self._stop.set()
