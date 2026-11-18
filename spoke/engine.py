"""Движок диктовки без интерфейса: клавиша → запись → распознавание → вставка. Меню и иконка (app.py) только показывают состояние."""
from __future__ import annotations

import logging
import queue
from collections import deque
import threading
import time

from spoke import inject, sounds
from spoke.config import Config
from spoke.hotkey import HoldGate, normalize_key
from spoke.recorder import Recorder
from spoke.streaming import StreamSession
from spoke.transcribe import SAMPLE_RATE, Transcriber, is_silence

logger = logging.getLogger("spoke")
IDLE, LOADING, RECORDING, WORKING = "idle", "loading", "recording", "working"


class Engine:
    def __init__(self, config: Config, notify=lambda title, text: None, recorder=None, transcriber=None, paste=inject.paste_text):
        self.config, self.notify, self.paste = config, notify, paste
        self.recorder = recorder or Recorder(config.max_seconds)
        self.transcriber = transcriber or Transcriber(config.model)
        self.state = LOADING
        self.last_text = ""
        self.history: deque[str] = deque(maxlen=max(1, config.history_size))
        self.last_seconds = 0.0
        self.last_error = ""
        self._session: StreamSession | None = None
        self.locked = False
        self.recording_since = 0.0
        self._last_paste_at: float | None = None
        self._jobs: queue.Queue = queue.Queue()
        self.gate = self._make_gate()
        self._listener = None
        threading.Thread(target=self._worker, daemon=True).start()

    def _make_gate(self) -> HoldGate:
        return HoldGate(self.config.hotkey, self.config.hold_ms, self._start, self._stop, self._cancel, on_lock=self._start_locked,
                        double_tap_ms=self.config.double_tap_ms, on_press=self._prewarm)

    def reload_hotkey(self) -> None:
        self.gate = self._make_gate()

    def start_listening(self) -> None:
        self._start_listener()
        threading.Thread(target=self._ticker, daemon=True).start()
        threading.Thread(target=self._keep_warm, daemon=True).start()

    def restart_listening(self) -> None:
        if self._listener is not None:
            self._listener.stop()
        self._start_listener()

    def _start_listener(self) -> None:
        from pynput import keyboard

        def down(key):
            self.gate.key_down(normalize_key(key))

        def up(key):
            self.gate.key_up(normalize_key(key))
        self._listener = keyboard.Listener(on_press=down, on_release=up)
        self._listener.daemon = True
        self._listener.start()

    def _prewarm(self) -> None:
        if self.state == IDLE and hasattr(self.transcriber, "prewarm"):
            threading.Thread(target=self._safe_prewarm, daemon=True).start()

    def _safe_prewarm(self) -> None:
        try:
            self.transcriber.prewarm()
        except Exception:
            logger.exception("Прогрев модели не удался")

    def _keep_warm(self) -> None:
        while True:
            seconds = self.config.keep_warm_seconds
            time.sleep(seconds if seconds > 0 else 5)
            if seconds <= 0 or not getattr(self.transcriber, "ready", False) or self.state not in (IDLE, RECORDING):
                continue
            try:
                self.transcriber.touch()
                if self.state == IDLE:
                    self.transcriber.prewarm(min_idle=0)
            except Exception:
                logger.exception("Не удалось поддержать модель в памяти")

    def resize_history(self) -> None:
        self.history = deque(self.history, maxlen=max(1, self.config.history_size))

    def _ticker(self) -> None:
        while True:
            self.gate.tick()
            time.sleep(0.02)

    def _start(self) -> None:
        if self.state != IDLE:
            return
        try:
            self.recorder.start()
        except Exception as error:
            self.last_error = f"микрофон недоступен: {error}"
            logger.exception("Не удалось начать запись")
            self.notify("Spoke", "Нет доступа к микрофону. Разрешите его в «Системные настройки → Конфиденциальность → Микрофон».")
            sounds.play(sounds.ERROR, self.config.sounds)
            return
        self.state = RECORDING
        self.recording_since = time.monotonic()
        sounds.play(sounds.START, self.config.sounds)
        if self.config.streaming and hasattr(self.recorder, "snapshot"):
            c = self.config
            self._session = StreamSession(self.recorder, self.transcriber, c.whisper_language(), c.prompt(), c.chunk_min_seconds,
                                          c.chunk_max_seconds, c.chunk_pause_seconds, c.silence_rms).start()

    def _start_locked(self) -> None:
        self.locked = True
        self._start()
        if self.state != RECORDING:
            self.locked = False
        else:
            self.recorder.max_seconds = max(self.recorder.max_seconds, self.config.locked_max_seconds)

    def finish_locked(self) -> None:
        if self.gate.release_lock():
            self._stop()

    def cancel_locked(self) -> None:
        if self.gate.release_lock():
            self._cancel()

    def _cancel(self) -> None:
        if self.state == RECORDING:
            if self._session is not None:
                self._session.cancel()
                self._session = None
            self.recorder.cancel()
            self.locked = False
            self.state = IDLE

    def _stop(self) -> None:
        if self.state != RECORDING:
            return
        audio = self.recorder.stop()
        self.locked = False
        self.state = WORKING
        sounds.play(sounds.STOP, self.config.sounds)
        session, self._session = self._session, None
        self._jobs.put((audio, session))

    def _worker(self) -> None:
        try:
            self.transcriber.warm_up()
        except Exception as error:
            self.last_error = f"модель не загрузилась: {error}"
            logger.exception("Не удалось загрузить модель")
            self.notify("Spoke", "Модель распознавания не загрузилась.")
        self.state = IDLE
        while True:
            audio, session = self._jobs.get()
            self.process(audio, session)

    def paste_from_history(self, index: int = 0) -> bool:
        """Вставить прежнюю диктовку туда, где сейчас курсор (из меню: даём меню закрыться и вернуть фокус)."""
        if index >= len(self.history):
            return False
        text = self.history[index]
        threading.Timer(0.3, self.paste, args=(text, self.config.restore_clipboard, False)).start()
        return True

    def _joined(self, text: str) -> str:
        recent = self._last_paste_at is not None and time.monotonic() - self._last_paste_at <= self.config.join_window_seconds
        if self.config.auto_space and recent and not self.config.trailing_space and not text[0].isspace():
            return " " + text
        return text

    def process(self, audio, session: StreamSession | None = None) -> str:
        """Звук → текст → вставка. Возвращает вставленный текст (пустая строка, если нечего вставлять).
        С потоковой сессией большая часть речи уже распознана: остаётся хвост."""
        started = time.monotonic()
        seconds = len(audio) / SAMPLE_RATE
        text, used = "", False
        try:
            if seconds >= self.config.min_seconds and not is_silence(audio, self.config.silence_rms):
                used = True
                text = session.finish(audio) if session is not None else self.transcriber.transcribe(audio, self.config.whisper_language(), self.config.prompt())
                if text:
                    self.paste(self._joined(text), self.config.restore_clipboard, self.config.trailing_space)
                    self._last_paste_at = time.monotonic()
        except Exception as error:
            text = ""
            self.last_error = str(error)
            logger.exception("Ошибка диктовки")
            self.notify("Spoke", f"Не получилось: {error}")
            sounds.play(sounds.ERROR, self.config.sounds)
        else:
            self.last_text, self.last_seconds, self.last_error = text, time.monotonic() - started, ""
            if text:
                self.history.appendleft(text)
            logger.info("записано %.1f с, обработано за %.1f с, символов %d", seconds, self.last_seconds, len(text))
        finally:
            if session is not None and not used:
                session.cancel()
            self.state = IDLE
        return text
