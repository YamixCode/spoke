"""Глобальные клавиши через Quartz Event Tap: pynput на новых macOS падает, обращаясь к раскладке клавиатуры из фонового потока."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger("spoke")

KEY_DOWN, KEY_UP, FLAGS_CHANGED = 10, 11, 12
KEYCODES = {59: "ctrl", 62: "ctrl", 61: "right_option", 58: "option", 55: "cmd", 54: "right_cmd", 60: "right_shift", 56: "shift", 53: "esc",
            105: "f13", 107: "f14", 113: "f15", 106: "f16", 64: "f17", 79: "f18", 80: "f19"}
FLAG_MASKS = {"ctrl": 0x40000, "option": 0x80000, "right_option": 0x80000, "cmd": 0x100000, "right_cmd": 0x100000, "shift": 0x20000, "right_shift": 0x20000}
MODIFIER_KEYS = {54: 0x100000, 55: 0x100000, 56: 0x20000, 57: 0x10000, 58: 0x80000,
                 59: 0x40000, 60: 0x20000, 61: 0x80000, 62: 0x40000, 63: 0x800000}
MODIFIER_SIDES = {54: 0x10, 55: 0x08, 56: 0x02, 58: 0x20, 59: 0x01, 60: 0x04, 61: 0x40, 62: 0x2000}
SIDE_MASK = sum(MODIFIER_SIDES.values())
FUNCTION_KEYS = {64, 79, 80, 90, 96, 97, 98, 99, 100, 101, 103, 105, 106, 107, 109, 111, 113,
                 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126}
MODIFIERS = 0x9E0000
REPLAY_MARKER = 0x53504F4B45


@dataclass(frozen=True)
class Shortcut:
    keycode: int
    modifiers: int
    title: str

    @classmethod
    def parse(cls, value: str) -> Shortcut:
        prefix, key, modifiers, title = value.split(":", 3)
        keycode, flags = int(key), int(modifiers)
        if prefix != "key" or not 0 <= keycode <= 255 or keycode == 57 or flags & ~MODIFIERS or not title.strip() or len(title) > 80:
            raise ValueError(value)
        if flags & MODIFIER_KEYS.get(keycode, 0):
            raise ValueError(value)
        return cls(keycode, flags, title)

    @property
    def is_modifier(self) -> bool:
        return self.keycode in MODIFIER_KEYS

    def modifier_down(self, keycode: int, flags: int, held: bool) -> bool:
        own = MODIFIER_KEYS[self.keycode]
        if flags & MODIFIERS != self.modifiers | own:
            return False
        if self.modifiers:
            return held or modifier_pressed(keycode, flags)
        return (held or keycode == self.keycode) and modifier_pressed(self.keycode, flags)

    def matches_flags(self, flags: int) -> bool:
        mask = MODIFIERS & ~MODIFIER_KEYS[63] if self.keycode in FUNCTION_KEYS else MODIFIERS
        return flags & mask == self.modifiers


def modifier_pressed(keycode: int, flags: int) -> bool:
    side = MODIFIER_SIDES.get(keycode)
    return bool(flags & side) if side and flags & SIDE_MASK else bool(flags & MODIFIER_KEYS.get(keycode, 0))


def valid_shortcut(value: str) -> bool:
    if value in KEYCODES.values() and value != "esc":
        return True
    try:
        Shortcut.parse(value)
    except ValueError:
        return False
    return True


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
    def __init__(self, on_down: Callable[[str], None], on_up: Callable[[str], None], shortcut=None,
                 gate_state=lambda: "", suspended=lambda: False, on_suspend=lambda: None):
        self.on_down, self.on_up = on_down, on_up
        self.shortcut, self.gate_state, self.suspended, self.on_suspend = shortcut, gate_state, suspended, on_suspend
        self._tap = None
        self._loop = None
        self._stopped = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        import Quartz
        self._stopped.set()
        if self._tap is not None:
            Quartz.CGEventTapEnable(self._tap, False)
        if self._loop is not None:
            Quartz.CFRunLoopStop(self._loop)

    def _run(self) -> None:
        import Quartz

        shortcut = self.shortcut
        intercept = shortcut is not None and not shortcut.is_modifier
        active, pending, swallowed, was_suspended = False, None, False, False

        def replay(proxy, event):
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGEventSourceUserData, REPLAY_MARKER)
            Quartz.CGEventTapPostEvent(proxy, event)

        def release(proxy, event_type, keycode):
            nonlocal active, pending, swallowed
            if pending is not None:
                if self.gate_state() == "pending":
                    replay(proxy, pending)
                else:
                    swallowed = True
                pending = None
            if active:
                self.on_up("shortcut")
                active = False
            if event_type == KEY_UP and shortcut is not None and keycode == shortcut.keycode:
                consume, swallowed = swallowed, False
                return consume
            return False

        def callback(proxy, event_type, event, refcon):
            nonlocal active, pending, swallowed, was_suspended
            try:
                if self._stopped.is_set():
                    return event
                if event_type in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                    Quartz.CGEventTapEnable(self._tap, True)
                    return event
                if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUserData) == REPLAY_MARKER:
                    return event
                keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
                repeat = bool(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat))
                flags = Quartz.CGEventGetFlags(event)
                if self.suspended():
                    if not was_suspended:
                        release(proxy, event_type, keycode)
                        self.on_suspend()
                    was_suspended = True
                    return event
                was_suspended = False
                if shortcut is not None and shortcut.is_modifier and event_type == FLAGS_CHANGED:
                    matched = shortcut.modifier_down(keycode, flags, active)
                    if matched != active:
                        active = matched
                        (self.on_down if active else self.on_up)("shortcut")
                    return event
                if intercept:
                    if (active or swallowed) and event_type == KEY_DOWN and keycode == shortcut.keycode:
                        return None
                    if event_type == KEY_DOWN and keycode == shortcut.keycode and shortcut.matches_flags(flags) and not repeat:
                        self.on_down("shortcut")
                        if self.gate_state() == "pending":
                            active, pending = True, Quartz.CGEventCreateCopy(event)
                            return None
                    if event_type == KEY_UP and keycode == shortcut.keycode:
                        return None if release(proxy, event_type, keycode) else event
                    if active and event_type == FLAGS_CHANGED and not shortcut.matches_flags(flags):
                        release(proxy, event_type, keycode)
                    if active and event_type == KEY_DOWN and pending is not None:
                        if self.gate_state() == "pending":
                            replay(proxy, pending)
                        else:
                            swallowed = True
                        pending = None
                result = classify(event_type, keycode, flags, repeat)
                if result:
                    (self.on_down if result[1] else self.on_up)(result[0])
            except Exception:
                logger.exception("Ошибка обработки клавиши")
            return event

        mask = sum(Quartz.CGEventMaskBit(kind) for kind in (KEY_DOWN, KEY_UP, FLAGS_CHANGED))
        option = Quartz.kCGEventTapOptionDefault if intercept else Quartz.kCGEventTapOptionListenOnly
        tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap, option, mask, callback, None)
        if tap is None:
            logger.warning("Нет доступа к клавишам: выдайте «Мониторинг ввода»")
            return
        self._tap = tap
        self._loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._loop, Quartz.CFMachPortCreateRunLoopSource(None, tap, 0), Quartz.kCFRunLoopCommonModes)
        if not self._stopped.is_set():
            Quartz.CGEventTapEnable(tap, True)
            Quartz.CFRunLoopRun()
