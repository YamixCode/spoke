"""Зажатие клавиши как «нажми и говори».

Ctrl нужен и для обычных сочетаний (Ctrl+C), поэтому запись начинается не сразу, а когда клавишу держат дольше `hold_ms` и больше ничего не нажато.
Если пока клавиша зажата нажали любую другую (это сочетание), запись не начинается, а уже идущая отменяется. Двойное быстрое нажатие включает постоянную запись (LOCKED): следующее нажатие её завершает, Esc отменяет. Чистый автомат без зависимостей:
Когда задано действие тройного нажатия, двойное ждёт короткое окно для третьего.
`tick()` зовут часто (раз в 20 мс), время можно подменить в тестах."""
from __future__ import annotations

import time
from typing import Callable

IDLE, PENDING, RECORDING, BLOCKED, LOCKED, RELEASING = "idle", "pending", "recording", "blocked", "locked", "releasing"


class HoldGate:
    def __init__(self, target: str, hold_ms: int, on_start: Callable[[], None], on_stop: Callable[[], None],
                 on_cancel: Callable[[], None], clock: Callable[[], float] = time.monotonic,
                 on_lock: Callable[[], None] | None = None, double_tap_ms: int = 400,
                 on_press: Callable[[], None] | None = None, on_reformat: Callable[[], None] | None = None):
        self.target, self.hold, self.clock = target, hold_ms / 1000, clock
        self.on_start, self.on_stop, self.on_cancel = on_start, on_stop, on_cancel
        self.on_lock, self.double_tap = on_lock, double_tap_ms / 1000
        self.on_press = on_press
        self.on_reformat = on_reformat
        self._triple_wait = min(self.double_tap, 0.30)
        self._taps = 0
        self.state, self._since, self._last_tap = IDLE, 0.0, None

    def _clear_taps(self) -> None:
        self._last_tap, self._taps = None, 0

    def key_down(self, name: str | None) -> None:
        if name == self.target:
            if self.state == IDLE:
                if self.on_press:
                    self.on_press()
                now = self.clock()
                interval = self._triple_wait if self._taps == 2 else self.double_tap
                rapid = self._last_tap is not None and now - self._last_tap <= interval
                if self.on_lock and self.on_reformat is None and rapid:
                    self._clear_taps()
                    self.state = LOCKED
                    self.on_lock()
                else:
                    if not rapid:
                        self._clear_taps()
                    self.state, self._since = PENDING, now
            elif self.state == LOCKED:
                self.state = RELEASING
                self.on_stop()
        elif self.state == LOCKED:
            if name == "esc":
                self.state = IDLE
                self.on_cancel()
        elif name is not None:
            self._clear_taps()
            if self.state in (PENDING, RECORDING):
                if self.state == RECORDING:
                    self.on_cancel()
                self.state = BLOCKED

    def key_up(self, name: str | None) -> None:
        if name != self.target:
            return
        was = self.state
        if was == LOCKED:
            return
        self.state = IDLE
        if was == PENDING:
            self._last_tap = self.clock()
            if self.on_reformat is not None:
                self._taps += 1
                if self._taps == 3:
                    self._clear_taps()
                    self.on_reformat()
        elif was == RECORDING:
            self.on_stop()

    def release_lock(self) -> bool:
        """Постоянная запись завершена не клавишей (кнопка на плашке): возвращаемся в покой. `False`, если запись и не была постоянной."""
        if self.state != LOCKED:
            return False
        self.state = IDLE
        return True

    def next_delay(self) -> float | None:
        if self.state == PENDING:
            return max(0, self._since + self.hold - self.clock())
        if self.state == IDLE and self._taps == 2 and self._last_tap is not None:
            return max(0, self._last_tap + self._triple_wait - self.clock())
        return None

    def tick(self) -> None:
        now = self.clock()
        last_tap = self._last_tap
        if self.state == PENDING and now - self._since >= self.hold:
            self._clear_taps()
            self.state = RECORDING
            self.on_start()
        elif (self.state == IDLE and self.on_lock and self._taps == 2
              and last_tap is not None and now - last_tap >= self._triple_wait):
            self._clear_taps()
            self.state = LOCKED
            self.on_lock()
