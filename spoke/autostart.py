"""Запуск при входе в систему через LaunchAgent (включается из меню). Из собранного приложения запускает само приложение."""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.spoke.app"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
PLIST = LAUNCH_AGENTS / f"{LABEL}.plist"
ROOT = Path(__file__).resolve().parents[1]


def enabled() -> bool:
    return PLIST.exists()


def _arguments() -> tuple[list[str], str]:
    resources = os.environ.get("RESOURCEPATH")
    if resources:
        return ["/usr/bin/open", "-a", str(Path(resources).parents[1])], str(Path.home())
    return [sys.executable, "-m", "spoke"], str(ROOT)


def enable() -> None:
    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    arguments, directory = _arguments()
    PLIST.write_bytes(plistlib.dumps({"Label": LABEL, "ProgramArguments": arguments, "WorkingDirectory": directory, "RunAtLoad": True,
                                      "KeepAlive": False, "ProcessType": "Interactive",
                                      "StandardErrorPath": str(Path.home() / "Library" / "Logs" / "Spoke.err")}))


def disable() -> None:
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(PLIST)], capture_output=True)
    PLIST.unlink(missing_ok=True)
