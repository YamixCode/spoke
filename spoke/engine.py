"""Движок диктовки без интерфейса: клавиша → запись → распознавание → вставка. Меню и иконка (app.py) только показывают состояние."""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from functools import partial
from pathlib import Path

import numpy as np

from spoke import inject, sounds
from spoke.apple_speech import AppleTranscriber
from spoke.cleanup import Cleanup
from spoke.cloud import CloudKeys, CloudSession, format_field
from spoke.config import Config
from spoke.history import HistoryStore
from spoke.hotkey import LOCKED, HoldGate
from spoke.keys import KeyListener, Shortcut
from spoke.recorder import Recorder
from spoke.streaming import StreamSession
from spoke.text_field import EditableField
from spoke.transcribe import SAMPLE_RATE, is_silence

logger = logging.getLogger("spoke")
IDLE, LOADING, RECORDING, WORKING = "idle", "loading", "recording", "working"


class Engine:
    def __init__(self, config: Config, notify=lambda title, text: None, recorder=None, transcriber=None, paste=inject.paste_text,
                 history_path: Path | None = None, recovery_file: Path | None = None):
        self.config, self.notify, self.paste = config, notify, paste
        self.recovery_file = recovery_file
        self.recorder = recorder or Recorder(config.max_seconds, recovery_file)
        self.cleanup = Cleanup() if transcriber is None else None
        if self.cleanup is not None:
            self.cleanup.enabled = config.dictation_mode == "local"
        self.transcriber = transcriber if transcriber is not None else AppleTranscriber(self.cleanup)
        self._state_lock = threading.RLock()
        self._wake = threading.Event()
        self._recording_token = None
        self.on_state_change = lambda: None
        self.state = LOADING
        self.last_text = ""
        self.history: deque[str] = deque(maxlen=max(1, config.history_size))
        self._store = HistoryStore(history_path, config.history_size) if history_path else None
        if self._store and config.save_history:
            self.history.extend(self._store.load())
        self.last_seconds = 0.0
        self.last_error = ""
        self._session: StreamSession | CloudSession | None = None
        self.locked = False
        self.formatting_field = False
        self.field_formatted_at: float | None = None
        self.recording_since = 0.0
        self._last_paste_at: float | None = None
        self._jobs: queue.Queue = queue.Queue()
        self.capturing_shortcut = False
        self.gate = self._make_gate()
        self._listener = None
        threading.Thread(target=self._worker, daemon=True).start()

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        self._state = value
        if value == IDLE and self.cleanup is not None and not self.cleanup.enabled:
            self._jobs.put(self.cleanup.release_if_disabled)
        self.on_state_change()

    def reserve_update(self):
        with self._state_lock:
            if self.state != IDLE:
                return False
            self.state = LOADING
            return True

    def _make_gate(self) -> HoldGate:
        target = "shortcut" if self.config.hotkey.startswith("key:") else self.config.hotkey
        on_lock = self._start_locked if target != "shortcut" or Shortcut.parse(self.config.hotkey).is_modifier else None
        return HoldGate(target, self.config.hold_ms, self._start, self._stop, self._cancel, on_lock=on_lock,
                        double_tap_ms=self.config.double_tap_ms,
                        on_reformat=self._start_field_formatting if on_lock else None)

    def reload_hotkey(self) -> None:
        self.gate = self._make_gate()
        self._wake.set()
        if self._listener is not None:
            self.restart_listening()

    def start_listening(self) -> None:
        self._start_listener()
        threading.Thread(target=self._ticker, daemon=True).start()

    def restart_listening(self) -> None:
        if self._listener is not None:
            self._listener.stop()
        self._start_listener()

    def _start_listener(self) -> None:
        shortcut = Shortcut.parse(self.config.hotkey) if self.config.hotkey.startswith("key:") else None
        def dispatch(action, name):
            try:
                getattr(self.gate, action)(name)
            finally:
                self._wake.set()
        self._listener = KeyListener(partial(dispatch, "key_down"), partial(dispatch, "key_up"),
                                     shortcut=shortcut, gate_state=lambda: self.gate.state,
                                     suspended=lambda: self.capturing_shortcut, on_suspend=self._make_fresh_gate)
        self._listener.start()

    def _make_fresh_gate(self) -> None:
        self._cancel()
        self.gate = self._make_gate()

    def resize_history(self) -> None:
        self.history = deque(self.history, maxlen=max(1, self.config.history_size))

    def clear_history(self) -> None:
        self.history.clear()
        if self._store:
            self._store.clear()

    def apply_history_setting(self) -> None:
        if self._store and not self.config.save_history:
            self._store.clear()

    def _remember(self, text: str) -> None:
        self.history.appendleft(text)
        if self._store and self.config.save_history:
            try:
                self._store.add(text)
            except OSError:
                logger.exception("Не удалось сохранить историю")

    def _discard_journal(self) -> None:
        if hasattr(self.recorder, "discard_journal"):
            self.recorder.discard_journal()

    def _recover(self) -> None:
        path = self.recovery_file
        if path:
            candidates = [*path.parent.glob(path.stem + "-*" + path.suffix)]
            if path.exists():
                candidates.append(path)
            for candidate in sorted(candidates, key=lambda item: item.stat().st_mtime):
                self._recover_file(candidate)

    def _recover_file(self, path):
        if not path.exists():
            return
        try:
            audio = np.fromfile(path, dtype="<i2").astype(np.float32) / 32767
            if len(audio) >= self.config.min_seconds * SAMPLE_RATE and not is_silence(audio, self.config.silence_rms):
                text = self._transcribe(audio)
                if not text:
                    return
                self._remember(text)
                self.notify("Spoke", f"Восстановлена несохранённая диктовка ({len(text)} символов): она в истории.")
            path.unlink(missing_ok=True)
        except Exception:
            logger.exception("Не удалось восстановить диктовку")

    def _ticker(self) -> None:
        while True:
            self._wake.clear()
            self.gate.tick()
            self._wake.wait(self.gate.next_delay())

    def _at_limit(self, token):
        with self._state_lock:
            if self._recording_token is token and self.state == RECORDING:
                self.gate.release_lock()
                self._stop()
                self.notify("Spoke", "Достигнут предел длительности. Обрабатываю запись.")

    def _start(self) -> None:
        with self._state_lock:
            if self.state != IDLE:
                return
            keys = None
            if self._cloud_enabled:
                try:
                    keys = CloudKeys.read()
                except RuntimeError as error:
                    self.last_error = str(error)
                    self.notify("Spoke", self.last_error)
                    sounds.play(sounds.ERROR, self.config.sounds)
                    return
            try:
                self.recorder.max_seconds = self.config.locked_max_seconds if self.locked else self.config.max_seconds
                token = self._recording_token = object()
                self.recorder.on_limit = lambda: self._jobs.put(partial(self._at_limit, token))
                self.recorder.start()
            except Exception as error:
                self.last_error = f"микрофон недоступен: {error}"
                logger.exception("Не удалось начать запись")
                self.notify("Spoke", "Нет доступа к микрофону. Разрешите его в «Системные настройки → Конфиденциальность → Микрофон».")
                sounds.play(sounds.ERROR, self.config.sounds)
                return
            self.state = RECORDING
            self.field_formatted_at = None
            self.recording_since = time.monotonic()
            sounds.play(sounds.START, self.config.sounds)
            if keys is not None:
                self._session = CloudSession(keys, self.config.vocabulary, self.recorder.snapshot_from, self.notify).start()
            elif self.config.streaming and hasattr(self.recorder, "snapshot_from"):
                c = self.config
                self._session = StreamSession(self.recorder, self.transcriber, tuple(c.vocabulary), c.chunk_min_seconds,
                                              c.chunk_max_seconds, c.chunk_pause_seconds, c.silence_rms).start()

    def _start_locked(self) -> None:
        self.locked = True
        self._start()
        if self.state != RECORDING:
            self.locked = False
            self.gate.release_lock()
        elif self.gate.state != LOCKED:
            self._cancel()

    def finish_locked(self) -> None:
        if self.gate.release_lock():
            self._stop()

    def cancel_locked(self) -> None:
        if self.gate.release_lock():
            self._cancel()

    def _cancel(self) -> None:
        with self._state_lock:
            self._cancel_recording()

    def _cancel_recording(self):
        if self.state == RECORDING:
            if self._session is not None:
                self._session.cancel()
                self._session = None
            try:
                self.recorder.cancel()
            finally:
                self.locked = False
                self.state = IDLE

    def _stop(self) -> None:
        with self._state_lock:
            self._stop_recording()

    def _stop_recording(self):
        if self.state != RECORDING:
            return
        try:
            audio = self.recorder.stop()
        except Exception as error:
            self.locked = False
            if self._session:
                self._session.cancel()
                self._session = None
            self.last_error = str(error)
            self.state = IDLE
            self.notify("Spoke", self.last_error)
            return
        self.locked = False
        self.state = WORKING
        sounds.play(sounds.STOP, self.config.sounds)
        session, self._session = self._session, None
        self._jobs.put(partial(self.process, audio, session))

    def _start_field_formatting(self) -> None:
        with self._state_lock:
            if self.state != IDLE:
                return
            if self.config.dictation_mode != "cloud":
                self.notify("Spoke", "Оформление поля через Haiku доступно в облачном режиме.")
                return
            self.last_error = ""
            self.field_formatted_at = None
            self.formatting_field = True
            self.state = WORKING
            self._jobs.put(self._format_focused_field)

    def _format_focused_field(self) -> None:
        field = None
        started = time.monotonic()
        try:
            field = EditableField.capture()
            captured = time.monotonic()
            if len(field.text) > 20_000:
                raise RuntimeError("Для одной правки выберите поле до 20 000 символов.")
            edited = format_field(field.text, tuple(self.config.vocabulary))
            formatted = time.monotonic()
            if field.replace(edited):
                self.field_formatted_at = time.monotonic()
            logger.info("Оформление поля: чтение %.2f с, модель %.2f с, замена и проверка %.2f с",
                        captured - started, formatted - captured, time.monotonic() - formatted)
        except RuntimeError as error:
            self.last_error = str(error)
            logger.info("Оформление поля остановлено: %s", self.last_error)
            self.notify("Spoke", self.last_error)
        except Exception:
            logger.warning("Не удалось оформить текст поля")
            self.last_error = "Не удалось оформить текст поля. Исходный текст сохранён."
            self.notify("Spoke", self.last_error)
        finally:
            if hasattr(field, "close"):
                field.close()
            self.formatting_field = False
            self.state = IDLE

    def _worker(self) -> None:
        self._prepare_recognizer()
        self._recover()
        self.state = IDLE
        while True:
            job = self._jobs.get()
            try:
                job()
            except Exception:
                logger.exception("Ошибка фоновой задачи Spoke")

    def change_mode(self):
        if self.cleanup is not None:
            self.cleanup.enabled = self.config.dictation_mode == "local"
            if self.cleanup.enabled:
                self._jobs.put(self._prepare_recognizer)
            elif self.state == IDLE:
                self._jobs.put(self.cleanup.release_if_disabled)

    def _prepare_recognizer(self) -> None:
        if self._cloud_enabled:
            return
        try:
            self.transcriber.warm_up()
        except Exception as error:
            self.last_error = f"модель не загрузилась: {error}"
            logger.exception("Не удалось загрузить модель")
            self.notify("Spoke", self.last_error)
        else:
            self.last_error = ""

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

    @property
    def _cloud_enabled(self) -> bool:
        return self.cleanup is not None and self.config.dictation_mode == "cloud"

    def _transcribe(self, audio) -> str:
        if self._cloud_enabled:
            session = CloudSession(CloudKeys.read(), self.config.vocabulary, lambda offset: audio[offset:], self.notify)
            return session.start().finish(audio)
        return self.transcriber.transcribe(audio, self.config.vocabulary)

    def process(self, audio, session: StreamSession | CloudSession | None = None) -> str:
        """Звук → текст → вставка. Возвращает вставленный текст (пустая строка, если нечего вставлять).
        С потоковой сессией большая часть речи уже распознана: остаётся хвост."""
        started = time.monotonic()
        seconds = len(audio) / SAMPLE_RATE
        text, used = "", False
        try:
            if seconds >= self.config.min_seconds and not is_silence(audio, self.config.silence_rms):
                used = True
                text = session.finish(audio) if session is not None else self._transcribe(audio)
                if text:
                    self._remember(text)
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
            if text or not used:
                self._discard_journal()
            logger.info("записано %.1f с, обработано за %.1f с, символов %d", seconds, self.last_seconds, len(text))
        finally:
            if session is not None and not used:
                session.cancel()
            self.state = IDLE
        return text
