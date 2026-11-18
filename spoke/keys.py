"""Глобальные клавиши через Quartz Event Tap: pynput на новых macOS падает, обращаясь к раскладке клавиатуры из фонового потока."""
from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger("spoke")

KEY_DOWN, KEY_UP, FLAGS_CHANGED = 10, 11, 12
KEYCODES = {59: "ctrl", 62: "ctrl", 61: "right_option", 58: "option", 55: "cmd", 54: "right_cmd", 60: "right_shift", 56: "shift", 53: "esc",
            105: "f13", 107: "f14", 113: "f15", 106: "f16", 64: "f17", 79: "f18", 80: "f19"}
FLAG_MASKS = {"ctrl": 0x40000, "option": 0x80000, "right_option": 0x80000, "cmd": 0x100000, "right_cmd": 0x100000, "shift": 0x20000, "right_shift": 0x20000}


def classify(event_type: int, keycode: int, flags: int, autorepeat: bool = False) -> tuple[str, bool] | None:
    name = KEYCODES.get(keycode)
    if event_type == FLAGS_CHANGED:
        return (name, bool(flags & FLAG_MASKS[name])) if name in FLAG_MASKS else None
    if event_type == KEY_DOWN:
        return None if autorepeat else (name or "char", True)
    if event_type == KEY_UP:
        return name or "char", False
    return None


class KeyListener:
    def __init__(self, on_down: Callable[[str], None], on_up: Callable[[str], None]):
        self.on_down, self.on_up = on_down, on_up
        self._tap = None
        self._loop = None

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        import Quartz
        if self._tap is not None:
            Quartz.CGEventTapEnable(self._tap, False)
        if self._loop is not None:
            Quartz.CFRunLoopStop(self._loop)

    def _run(self) -> None:
        import Quartz

        def callback(proxy, event_type, event, refcon):
            try:
                if event_type in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                    Quartz.CGEventTapEnable(self._tap, True)
                    return event
                keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
                repeat = bool(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat))
                result = classify(event_type, keycode, Quartz.CGEventGetFlags(event), repeat)
                if result:
                    (self.on_down if result[1] else self.on_up)(result[0])
            except Exception:
                logger.exception("Ошибка обработки клавиши")
            return event

        mask = sum(Quartz.CGEventMaskBit(kind) for kind in (KEY_DOWN, KEY_UP, FLAGS_CHANGED))
        tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap, Quartz.kCGEventTapOptionListenOnly, mask, callback, None)
        if tap is None:
            logger.warning("Нет доступа к клавишам: выдайте «Мониторинг ввода»")
            return
        self._tap = tap
        self._loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._loop, Quartz.CFMachPortCreateRunLoopSource(None, tap, 0), Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        Quartz.CFRunLoopRun()
