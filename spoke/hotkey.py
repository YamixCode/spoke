"""Зажатие клавиши как «нажми и говори».

Ctrl нужен и для обычных сочетаний (Ctrl+C), поэтому запись начинается не сразу, а когда клавишу держат дольше `hold_ms` и больше ничего не нажато.
Если пока клавиша зажата нажали любую другую (это сочетание), запись не начинается, а уже идущая отменяется. Двойное быстрое нажатие включает постоянную запись (LOCKED): следующее нажатие её завершает, Esc отменяет. Чистый автомат без зависимостей:
`tick()` зовут часто (раз в 20 мс), время можно подменить в тестах."""
from __future__ import annotations

import time
from typing import Callable

IDLE, PENDING, RECORDING, BLOCKED, LOCKED, RELEASING = "idle", "pending", "recording", "blocked", "locked", "releasing"


def normalize_key(key) -> str | None:
    """Имя клавиши pynput в наши имена («ctrl», «right_option», «f13»…); `None` — не интересует."""
    name = getattr(key, "name", None)
    if name is None:
        return "char"
    return {"ctrl_l": "ctrl", "ctrl_r": "ctrl", "alt_r": "right_option", "alt_gr": "right_option", "alt": "option", "alt_l": "option",
            "cmd_r": "right_cmd", "cmd": "cmd", "cmd_l": "cmd", "shift_r": "right_shift", "shift": "shift", "shift_l": "shift"}.get(name, name)


class HoldGate:
    def __init__(self, target: str, hold_ms: int, on_start: Callable[[], None], on_stop: Callable[[], None],
                 on_cancel: Callable[[], None], clock: Callable[[], float] = time.monotonic,
                 on_lock: Callable[[], None] | None = None, double_tap_ms: int = 400,
                 on_press: Callable[[], None] | None = None):
        self.target, self.hold, self.clock = target, hold_ms / 1000, clock
        self.on_start, self.on_stop, self.on_cancel = on_start, on_stop, on_cancel
        self.on_lock, self.double_tap = on_lock, double_tap_ms / 1000
        self.on_press = on_press
        self.state, self._since, self._last_tap = IDLE, 0.0, None

    def key_down(self, name: str | None) -> None:
        if name == self.target:
            if self.state == IDLE:
                if self.on_press:
                    self.on_press()
                if self.on_lock and self._last_tap is not None and self.clock() - self._last_tap <= self.double_tap:
                    self._last_tap, self.state = None, LOCKED
                    self.on_lock()
                else:
                    self.state, self._since = PENDING, self.clock()
            elif self.state == LOCKED:
                self.state = RELEASING
                self.on_stop()
        elif self.state == LOCKED:
            if name == "esc":
                self.state = IDLE
                self.on_cancel()
        elif name is not None and self.state in (PENDING, RECORDING):
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
        elif was == RECORDING:
            self.on_stop()

    def release_lock(self) -> bool:
        """Постоянная запись завершена не клавишей (кнопка на плашке): возвращаемся в покой. `False`, если запись и не была постоянной."""
        if self.state != LOCKED:
            return False
        self.state = IDLE
        return True

    def tick(self) -> None:
        if self.state == PENDING and self.clock() - self._since >= self.hold:
            self.state = RECORDING
            self.on_start()
