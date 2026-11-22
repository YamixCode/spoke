"""Вставка текста в приложение, где стоит курсор: через буфер обмена и Cmd+V (работает везде, быстро, поддерживает кириллицу).
Прежний буфер обмена возвращается. Нужно разрешение «Универсальный доступ» (Accessibility) для программы, из которой запущено."""
from __future__ import annotations

import os
import subprocess
import time

from spoke.keys import REPLAY_MARKER

_ENV = {**os.environ, "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"}


def get_clipboard() -> str | None:
    try:
        return subprocess.run(["pbpaste"], capture_output=True, env=_ENV, timeout=2).stdout.decode("utf-8", "replace")
    except Exception:
        return None


def set_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), env=_ENV, timeout=2, check=False)


def press_command_key(keycode: int, pid: int | None = None) -> None:
    import Quartz
    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    for pressed in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(source, keycode, pressed)
        Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGEventSourceUserData, REPLAY_MARKER)
        if pid is None:
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        else:
            Quartz.CGEventPostToPid(pid, event)


def press_cmd_v() -> None:
    press_command_key(9)


def _snapshot_clipboard():
    from AppKit import NSPasteboard, NSPasteboardItem

    board = NSPasteboard.generalPasteboard()
    initial_count = board.changeCount()
    saved = []
    for item in board.pasteboardItems() or []:
        snapshot = NSPasteboardItem.alloc().init()
        for kind in item.types():
            data = item.dataForType_(kind)
            if data is None or not snapshot.setData_forType_(data, kind):
                raise RuntimeError("Буфер обмена нельзя безопасно сохранить. Текст поля не заменён.")
        saved.append(snapshot)
    if board.changeCount() != initial_count:
        raise RuntimeError("Буфер обмена изменился. Повторите оформление текста.")
    return board, initial_count, saved


def _restore_clipboard(board, own_count: int, saved) -> None:
    if board.changeCount() == own_count:
        board.clearContents()
        if saved:
            board.writeObjects_(saved)


def paste_into_application(text: str, pid: int | None, validate, confirm) -> str | None:
    """Paste through the editor while preserving all available clipboard formats."""
    from AppKit import NSPasteboardTypeString

    board, initial_count, saved = _snapshot_clipboard()
    validate()
    if board.changeCount() != initial_count:
        raise RuntimeError("Буфер обмена изменился. Повторите оформление текста.")

    own_count = board.clearContents()
    try:
        if not board.setString_forType_(text, NSPasteboardTypeString):
            raise RuntimeError("Не удалось подготовить вставку.")
        own_count = board.changeCount()
        validate()
        if board.changeCount() != own_count:
            raise RuntimeError("Буфер обмена изменился. Повторите оформление текста.")
        press_command_key(9, pid)
        return confirm()
    finally:
        _restore_clipboard(board, own_count, saved)


def copy_from_application(pid: int, validate) -> str | None:
    from AppKit import NSPasteboardTypeString

    board, initial_count, saved = _snapshot_clipboard()
    validate()
    if board.changeCount() != initial_count:
        raise RuntimeError("Буфер обмена изменился. Повторите оформление текста.")
    press_command_key(8, pid)  # C
    deadline = time.monotonic() + 0.35
    while time.monotonic() < deadline:
        copied_count = board.changeCount()
        if copied_count != initial_count:
            try:
                validate()
                return board.stringForType_(NSPasteboardTypeString)
            finally:
                _restore_clipboard(board, copied_count, saved)
        time.sleep(0.015)
    return None


def paste_text(text: str, restore: bool = True, trailing_space: bool = False) -> None:
    if not text:
        return
    text += " " if trailing_space else ""
    if restore:
        paste_into_application(text, None, lambda: None, lambda: time.sleep(0.30))
    else:
        set_clipboard(text)
        press_cmd_v()
