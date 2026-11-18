"""Настройки Spoke: файл ~/.config/spoke/config.json (создаётся при первом запуске, правится руками или из меню)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("SPOKE_CONFIG") or Path.home() / ".config" / "spoke" / "config.json")
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"


def bundled_model() -> str | None:
    resources = os.environ.get("RESOURCEPATH")
    path = Path(resources) / "model" if resources else None
    return str(path) if path and (path / "weights.safetensors").exists() else None
LANGUAGES = {"ru": "Русский", "en": "English", "auto": "Автоопределение"}
HOTKEY_NAMES = {"ctrl": "Ctrl", "right_option": "Правый Option", "right_cmd": "Правый Cmd", "right_shift": "Правый Shift",
                "f13": "F13", "f14": "F14", "f15": "F15", "f16": "F16", "f17": "F17", "f18": "F18", "f19": "F19"}
HOTKEYS = ("ctrl", "right_option", "right_cmd", "right_shift", "f13", "f14", "f15", "f16", "f17", "f18", "f19")


@dataclass
class Config:
    hotkey: str = "ctrl"
    hold_ms: int = 250
    language: str = "ru"
    model: str = DEFAULT_MODEL
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
    keep_warm_seconds: int = 45
    check_updates_on_start: bool = True
    github_token: str = ""
    history_size: int = 10
    double_tap_ms: int = 400
    locked_max_seconds: int = 1800
    auto_space: bool = True
    join_window_seconds: float = 90.0
    overlay_bottom_margin: float = 14.0
    overlay: bool = True
    vocabulary: list[str] = field(default_factory=list)

    def whisper_language(self) -> str | None:
        return None if self.language == "auto" else self.language

    def token(self) -> str:
        return self.github_token or os.environ.get("SPOKE_GITHUB_TOKEN", "")

    def prompt(self) -> str | None:
        words = ", ".join(self.vocabulary)
        return (f"Термины и имена: {words}." if words else None)


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
    if config.hotkey not in HOTKEYS:
        config.hotkey = "ctrl"
    if config.language not in ("ru", "en", "auto"):
        config.language = "ru"
    config.hold_ms = max(100, min(int(config.hold_ms), 2000))
    if config.model == DEFAULT_MODEL and bundled_model():
        config.model = bundled_model()
    return config


def save(config: Config, path: Path | None = None) -> None:
    path = Path(path or CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(config)
    if data["model"] == bundled_model():
        data["model"] = DEFAULT_MODEL
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)
