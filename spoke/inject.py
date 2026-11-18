"""Вставка текста в приложение, где стоит курсор: через буфер обмена и Cmd+V (работает везде, быстро, поддерживает кириллицу).
Прежний буфер обмена возвращается. Нужно разрешение «Универсальный доступ» (Accessibility) для программы, из которой запущено."""
from __future__ import annotations

import os
import subprocess
import threading
import time

_ENV = {**os.environ, "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"}


def get_clipboard() -> str | None:
    try:
        return subprocess.run(["pbpaste"], capture_output=True, env=_ENV, timeout=2).stdout.decode("utf-8", "replace")
    except Exception:
        return None


def set_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), env=_ENV, timeout=2, check=False)


def press_cmd_v() -> None:
    try:
        from pynput.keyboard import Controller, Key
        keyboard = Controller()
        with keyboard.pressed(Key.cmd):
            keyboard.press("v")
            keyboard.release("v")
    except Exception:
        subprocess.run(["osascript", "-e", 'tell application "System Events" to keystroke "v" using command down'], timeout=5, check=False)


def paste_text(text: str, restore: bool = True, trailing_space: bool = False) -> None:
    if not text:
        return
    previous = get_clipboard() if restore else None
    set_clipboard(text + (" " if trailing_space else ""))
    time.sleep(0.05)
    press_cmd_v()
    if restore and previous is not None:
        threading.Timer(0.3, set_clipboard, args=(previous,)).start()
