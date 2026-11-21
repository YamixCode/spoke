"""Окно настроек: оболочка AppKit с панелью разделов и системные формы SwiftUI из spoke/native."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import time

import objc
from AppKit import (NSApp, NSBackingStoreBuffered, NSMakeRect, NSObject, NSWindow,
                    NSWindowStyleMaskClosable, NSWindowStyleMaskTitled)

from spoke import __version__, autostart, config as cfg, permissions
from spoke.apple_speech import available as apple_speech_available
from spoke.cloud import PROVIDERS, keychain_read, keychain_remove, keychain_save
from spoke.native_bridge import load_library
from spoke.usage import key_fingerprint, usage_store

LOG_PATH = os.path.expanduser("~/Library/Logs/Spoke.log")
PANES = {"general": "Основные", "recognition": "Распознавание", "usage": "Расход API",
         "overlay": "Плашка и звук", "system": "Система"}


class _Controller(NSObject):
    owner = None

    def settingChanged_value_(self, key, value):
        self.owner.changed(str(key), str(value))

    def settingAction_(self, name):
        self.owner.action(str(name))

    def windowDidResignKey_(self, _):
        self.owner.bridge.stopCapture()


class SettingsWindow:
    def __init__(self, config: cfg.Config, engine, on_change=lambda key: None, on_update=lambda: None, on_preview=lambda mode: None):
        self.config, self.engine, self.on_change, self.on_update = config, engine, on_change, on_update
        self.on_preview = on_preview
        self._usage_fingerprints = {}
        self._usage_refresh_at = 0.0
        load_library()
        self.controller = _Controller.alloc().init()
        self.controller.owner = self
        self.bridge = objc.lookUpClass("SpokeSettingsBridge").alloc().initWithConfig_delegate_(json.dumps(self._snapshot()), self.controller)
        self.selected_pane = "general"
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 560, 300), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable, NSBackingStoreBuffered, False)
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self.controller)
        self.window.setTitlebarAppearsTransparent_(True)
        self.content = self.bridge.makeContentView()
        self.window.setContentView_(self.content)
        self.select("general")
        self.window.center()

    def _snapshot(self) -> dict:
        c = self.config
        return {"version": __version__, "hotkey_title": c.hotkey_title(), "autostart": c.autostart, "overlay": c.overlay,
                "overlay_bottom_margin": c.overlay_bottom_margin, "overlay_size": c.overlay_size, "sounds": c.sounds,
                "check_updates_on_start": c.check_updates_on_start, "apple_available": apple_speech_available(),
                "cleanup_status": self._cleanup_status(), "dictation_mode": c.dictation_mode}

    def _cleanup_status(self) -> str:
        return "Локальный режим выключен" if self.config.dictation_mode == "cloud" else self.engine.cleanup.status

    def _refresh_keys(self, message: str = "") -> None:
        values = {}
        try:
            for provider in PROVIDERS:
                values[provider + "_saved"] = bool(keychain_read(provider))
        except RuntimeError as error:
            message = str(error)
        self.bridge.update_(json.dumps({**values, "cloud_message": message}))

    def refresh_usage(self, *, reload_keys: bool = False) -> None:
        if not reload_keys and (self.selected_pane != "usage" or time.monotonic() < self._usage_refresh_at):
            return
        self._usage_refresh_at = time.monotonic() + 2
        message = ""
        try:
            if reload_keys:
                self._usage_fingerprints = {provider: key_fingerprint(keychain_read(provider)) for provider in PROVIDERS}
            both = {period: usage_store.totals(self._usage_fingerprints, this_month=period == "month")
                    for period in ("month", "all")}
        except (OSError, sqlite3.Error, RuntimeError):
            both = {}
            message = "Не удалось прочитать расход API."
        self.bridge.update_(json.dumps({"api_usage": both, "usage_message": message}))

    def select(self, identifier: str) -> None:
        self.bridge.stopCapture()
        if identifier == "recognition":
            self._refresh_keys()
        elif identifier == "usage":
            self.refresh_usage(reload_keys=True)
        self.selected_pane = identifier
        self.window.setTitle_(PANES[identifier])
        self.bridge.selectPane_(identifier)
        self.content.layoutSubtreeIfNeeded()
        size = self.content.fittingSize()
        frame = self.window.frameRectForContentRect_(NSMakeRect(0, 0, size.width, size.height))
        current = self.window.frame()
        frame.origin.x = current.origin.x
        frame.origin.y = current.origin.y + current.size.height - frame.size.height
        if frame != current:
            self.window.setFrame_display_(frame, False)

    def refresh_permissions(self) -> None:
        def mark(granted: bool) -> str:
            return "✓ Выдано" if granted else "✕ Не выдано"
        self.bridge.update_(json.dumps({"accessibility": mark(permissions.accessibility_granted()), "input_monitoring": mark(permissions.input_monitoring_granted()),
                                        "microphone": "Спросит при записи", "cleanup_status": self._cleanup_status()}))

    def show(self) -> None:
        self.refresh_permissions()
        if self.selected_pane == "recognition":
            self._refresh_keys()
        elif self.selected_pane == "usage":
            self.refresh_usage(reload_keys=True)
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    @property
    def visible(self) -> bool:
        return bool(self.window.isVisible())

    def update_status(self, message: str, checking: bool = False) -> None:
        self.bridge.update_(json.dumps({"update_status": message, "checking_update": checking}))

    def changed(self, key: str, value: str) -> None:
        if key.startswith("api_key_") and (provider := key.removeprefix("api_key_")) in PROVIDERS:
            try:
                keychain_save(provider, value)
            except (ValueError, RuntimeError) as error:
                self._refresh_keys(str(error))
            else:
                self._refresh_keys()
            return
        previous = getattr(self.config, key, None)
        try:
            cfg.set_option(self.config, key, value)
        except (KeyError, ValueError):
            return
        try:
            if key == "autostart":
                (autostart.enable if self.config.autostart else autostart.disable)()
            cfg.save(self.config)
        except OSError:
            setattr(self.config, key, previous)
            self.bridge.update_(json.dumps(self._snapshot()))
            logging.getLogger("spoke").exception("Не удалось сохранить настройку %s", key)
            return
        if key == "hotkey":
            self.engine.reload_hotkey()
            self.bridge.update_(json.dumps({"hotkey_title": self.config.hotkey_title()}))
        elif key == "dictation_mode":
            self.refresh_permissions()
        self.on_change(key)

    def action(self, name: str) -> None:
        panes = {"open_accessibility": permissions.ACCESSIBILITY_PANE, "open_input": permissions.INPUT_MONITORING_PANE,
                 "open_microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"}
        if name in ("capture_start", "capture_stop"):
            self.engine.capturing_shortcut = name == "capture_start"
        elif name in panes:
            subprocess.run(["open", panes[name]], check=False)
        elif name == "update":
            self.on_update()
        elif name.startswith("pane_") and (identifier := name.removeprefix("pane_")) in PANES:
            self.select(identifier)
        elif name in ("preview_voice", "preview_loading"):
            self.on_preview("record" if name == "preview_voice" else "work")
        elif name == "show_log":
            subprocess.run(["open", "-R", LOG_PATH], check=False)
        elif name.startswith("remove_key_") and (provider := name.removeprefix("remove_key_")) in PROVIDERS:
            try:
                keychain_remove(provider)
            except RuntimeError as error:
                self._refresh_keys(str(error))
            else:
                self._refresh_keys()
