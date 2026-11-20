"""Запись с микрофона: 16 кГц, моно, в памяти и одновременно в файл-журнал, чтобы сказанное пережило падение приложения."""
from __future__ import annotations

import os
import queue
import threading
from bisect import bisect_right
from pathlib import Path

import numpy as np

from spoke.transcribe import SAMPLE_RATE


class Recorder:
    def __init__(self, max_seconds: int = 180, journal: Path | None = None):
        self.max_seconds = max_seconds
        self.journal = journal
        self._journal_queue: queue.Queue | None = None
        self._journal_writer: threading.Thread | None = None
        self._chunks: list[tuple[int, np.ndarray]] = []
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
            self._open_journal()

            def callback(indata, frames, time_info, status):           # noqa: ARG001
                self.level = float(np.sqrt(np.mean(np.square(indata[:, 0]))))
                offset = self._chunks[-1][0] if self._chunks else 0
                remaining = self.max_seconds * SAMPLE_RATE - offset
                if remaining > 0:
                    chunk = indata[:remaining, 0].copy()
                    self._chunks.append((offset + len(chunk), chunk))
                    if self._journal_queue is not None:
                        self._journal_queue.put(chunk)
            self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback)
            self._stream.start()

    def snapshot(self) -> np.ndarray:
        return self.snapshot_from(0)

    def snapshot_from(self, offset: int) -> np.ndarray:
        chunks = self._chunks
        first = bisect_right(chunks, offset, key=lambda entry: entry[0])
        pending = chunks[first:]
        if not pending:
            return np.zeros(0, dtype=np.float32)
        end, chunk = pending[0]
        return np.concatenate([chunk[offset - (end - len(chunk)):], *(data for _, data in pending[1:])])

    def stop(self) -> np.ndarray:
        """Останавливает запись и возвращает звук (пустой массив, если не писали)."""
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        self.level = 0.0
        self._close_journal()
        chunks, self._chunks = self._chunks, []
        return np.concatenate([data for _, data in chunks]) if chunks else np.zeros(0, dtype=np.float32)

    def cancel(self) -> None:
        self.stop()
        self.discard_journal()

    def discard_journal(self) -> None:
        if self.journal is not None:
            self.journal.unlink(missing_ok=True)

    def _open_journal(self) -> None:
        if self.journal is None:
            return
        self.journal.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = os.fdopen(os.open(self.journal, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb")
        pending: queue.Queue = queue.Queue()
        self._journal_queue = pending

        def write() -> None:
            with handle:
                while (chunk := pending.get()) is not None:
                    handle.write((np.clip(chunk, -1.0, 1.0) * 32767).astype("<i2").tobytes())
                    handle.flush()
        self._journal_writer = threading.Thread(target=write, daemon=True)
        self._journal_writer.start()

    def _close_journal(self) -> None:
        if self._journal_queue is not None:
            self._journal_queue.put(None)
            self._journal_writer.join(timeout=3)
            self._journal_queue = self._journal_writer = None
