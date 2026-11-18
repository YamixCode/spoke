"""Разрешения macOS: «Универсальный доступ» (вставка), «Мониторинг ввода» (глобальная клавиша). Микрофон macOS спросит сам при первой записи."""
from __future__ import annotations

import subprocess

ACCESSIBILITY_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
INPUT_MONITORING_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"


def accessibility_granted(prompt: bool = False) -> bool:
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: prompt}))
    except Exception:
        return True


def input_monitoring_granted(prompt: bool = False) -> bool:
    try:
        import Quartz
        if Quartz.CGPreflightListenEventAccess():
            return True
        if prompt:
            Quartz.CGRequestListenEventAccess()
        return False
    except Exception:
        return True


def all_granted() -> bool:
    return accessibility_granted() and input_monitoring_granted()


def open_settings() -> None:
    pane = INPUT_MONITORING_PANE if not input_monitoring_granted() else ACCESSIBILITY_PANE
    subprocess.run(["open", pane], check=False)
