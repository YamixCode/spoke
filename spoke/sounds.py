"""Короткие системные звуки начала и конца записи."""
from __future__ import annotations

import subprocess

START, STOP, DONE, ERROR = "Tink", "Pop", "Morse", "Basso"


def play(name: str, enabled: bool = True) -> None:
    if enabled:
        subprocess.Popen(["afplay", f"/System/Library/Sounds/{name}.aiff"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
