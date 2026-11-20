"""Настройки Spoke: файл ~/.config/spoke/config.json (создаётся при первом запуске, правится руками или из меню)."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from spoke.keys import Shortcut, valid_shortcut

DATA_DIR = Path.home() / "Library" / "Application Support" / "Spoke"
HISTORY_FILE = DATA_DIR / "history.jsonl"
RECOVERY_FILE = DATA_DIR / "recovery" / "recording.pcm"
CONFIG_PATH = Path(os.environ.get("SPOKE_CONFIG") or Path.home() / ".config" / "spoke" / "config.json")
LEGACY_CONFIG_PATH = Path.home() / ".config" / "localflow" / "config.json"
_SAVE_LOCK = threading.Lock()
HOTKEY_NAMES = {"ctrl": "Ctrl", "option": "Option", "right_option": "Правый Option", "cmd": "Cmd", "right_cmd": "Правый Cmd",
                "shift": "Shift", "right_shift": "Правый Shift"}


@dataclass
class Config:
    hotkey: str = "ctrl"
    hold_ms: int = 250
    sounds: bool = False
    restore_clipboard: bool = True
    trailing_space: bool = False
    min_seconds: float = 0.4
    max_seconds: int = 180
    silence_rms: float = 0.004
    streaming: bool = True
    chunk_min_seconds: float = 3.0
    chunk_max_seconds: float = 14.0
    chunk_pause_seconds: float = 0.6
    check_updates_on_start: bool = True
    save_history: bool = True
    history_size: int = 15
    double_tap_ms: int = 400
    locked_max_seconds: int = 1800
    auto_space: bool = True
    join_window_seconds: float = 90.0
    overlay_bottom_margin: float = 14.0
    overlay: bool = True
    autostart: bool = True
    dictation_mode: str = "local"
    vocabulary: list[str] = field(default_factory=list)

    def apply_app_defaults(self) -> None:
        self.hold_ms = 250
        self.double_tap_ms = 400
        self.history_size = 15
        self.auto_space = True
        self.streaming = True

    def token(self) -> str:
        return os.environ.get("SPOKE_GITHUB_TOKEN", "")

    def hotkey_title(self) -> str:
        if self.hotkey.startswith("key:"):
            return Shortcut.parse(self.hotkey).title
        return HOTKEY_NAMES.get(self.hotkey, self.hotkey.upper())

BOOLEANS = ("auto_space", "autostart", "overlay", "sounds", "streaming", "check_updates_on_start")
INTEGERS = ("hold_ms", "double_tap_ms", "history_size")


def set_option(config: "Config", key: str, text: str) -> None:
    if key == "vocabulary":
        config.vocabulary = [line.strip() for line in text.splitlines() if line.strip()]
    elif key == "dictation_mode" and text in ("local", "cloud"):
        config.dictation_mode = text
    elif key in BOOLEANS:
        setattr(config, key, text.strip().lower() == "true")
    elif key in INTEGERS:
        setattr(config, key, int(float(text)))
    elif key == "overlay_bottom_margin":
        config.overlay_bottom_margin = float(text)
    elif key == "hotkey" and valid_shortcut(text):
        config.hotkey = text
    else:
        raise KeyError(key)


def load(path: Path | None = None) -> Config:
    path = Path(path or CONFIG_PATH)
    config = Config()
    sources = [path] + ([LEGACY_CONFIG_PATH] if path == CONFIG_PATH else [])
    raw = {}
    for source in sources:
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
            break
        except (OSError, ValueError):
            continue
    for key, value in raw.items():
        if hasattr(config, key) and isinstance(value, type(getattr(config, key))):
            setattr(config, key, value)
    if not valid_shortcut(config.hotkey):
        config.hotkey = "ctrl"
    if config.dictation_mode not in ("local", "cloud"):
        config.dictation_mode = "local"
    config.hold_ms = max(100, min(int(config.hold_ms), 2000))
    return config


def save(config: Config, path: Path | None = None) -> None:
    path = Path(path or CONFIG_PATH)
    with _SAVE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
        path.chmod(0o600)
