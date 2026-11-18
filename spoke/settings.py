"""Окно настроек: оболочка AppKit с панелью разделов и системные формы SwiftUI из spoke/native."""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
from pathlib import Path

import objc
from AppKit import (NSApp, NSBackingStoreBuffered, NSImage, NSMakeRect, NSObject, NSToolbar, NSToolbarItem, NSView, NSWindow, NSWindowStyleMaskClosable,
                    NSWindowStyleMaskTitled, NSWindowToolbarStylePreference)

from spoke import __version__, autostart, config as cfg, permissions

LIBRARY = "libSpokeUI.dylib"
LOG_PATH = os.path.expanduser("~/Library/Logs/Spoke.log")
PANES = (("general", "Основные", "gearshape"), ("overlay", "Плашка и звук", "waveform"), ("vocabulary", "Словарь", "text.book.closed"),
         ("system", "Система", "lock.shield"))
RESTART_HOTKEY = ("hotkey", "hold_ms", "double_tap_ms")


def library_path() -> Path:
    candidates = [Path(__file__).resolve().parent / "native" / LIBRARY]
    if os.environ.get("RESOURCEPATH"):
        candidates.append(Path(os.environ["RESOURCEPATH"]) / "native" / LIBRARY)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError("Не найдена библиотека окна настроек: соберите её скриптом scripts/build_native.sh")


class _Controller(NSObject):
    owner = None

    def pane_(self, sender):
        self.owner.select(str(sender.itemIdentifier()))

    def settingChanged_value_(self, key, value):
        self.owner.changed(str(key), str(value))

    def settingAction_(self, name):
        self.owner.action(str(name))

    def toolbarAllowedItemIdentifiers_(self, toolbar):
        return [pane[0] for pane in PANES]

    def toolbarDefaultItemIdentifiers_(self, toolbar):
        return [pane[0] for pane in PANES]

    def toolbarSelectableItemIdentifiers_(self, toolbar):
        return [pane[0] for pane in PANES]

    def toolbar_itemForItemIdentifier_willBeInsertedIntoToolbar_(self, toolbar, identifier, flag):
        pane = next(pane for pane in PANES if pane[0] == identifier)
        item = NSToolbarItem.alloc().initWithItemIdentifier_(identifier)
        item.setLabel_(pane[1])
        item.setImage_(NSImage.imageWithSystemSymbolName_accessibilityDescription_(pane[2], pane[1]))
        item.setTarget_(self)
        item.setAction_("pane:")
        return item


class SettingsWindow:
    def __init__(self, config: cfg.Config, engine, on_change=lambda key: None, on_update=lambda: ""):
        self.config, self.engine, self.on_change, self.on_update = config, engine, on_change, on_update
        ctypes.CDLL(str(library_path()))
        self.controller = _Controller.alloc().init()
        self.controller.owner = self
        self.bridge = objc.lookUpClass("SpokeSettingsBridge").alloc().initWithConfig_delegate_(json.dumps(self._snapshot()), self.controller)
        self.panes: dict[str, NSView] = {}
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 560, 300), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable, NSBackingStoreBuffered, False)
        self.window.setReleasedWhenClosed_(False)
        self.toolbar = NSToolbar.alloc().initWithIdentifier_("spoke.settings")
        self.toolbar.setDelegate_(self.controller)
        self.toolbar.setAllowsUserCustomization_(False)
        self.window.setToolbar_(self.toolbar)
        self.window.setToolbarStyle_(NSWindowToolbarStylePreference)
        self.window.center()
        self.select("general", animate=False)

    def _snapshot(self) -> dict:
        c = self.config
        return {"version": __version__, "hotkeys": [[key, title] for key, title in cfg.HOTKEY_NAMES.items()],
                "languages": [[key, title] for key, title in cfg.LANGUAGES.items()], "hotkey": c.hotkey, "language": c.language,
                "hold_ms": c.hold_ms, "double_tap_ms": c.double_tap_ms, "history_size": c.history_size, "auto_space": c.auto_space,
                "autostart": autostart.enabled(), "overlay": c.overlay, "overlay_bottom_margin": c.overlay_bottom_margin, "sounds": c.sounds,
                "streaming": c.streaming, "keep_warm_seconds": c.keep_warm_seconds, "check_updates_on_start": c.check_updates_on_start,
                "vocabulary": c.vocabulary}

    def select(self, identifier: str, animate: bool = True) -> None:
        pane = self.panes.setdefault(identifier, self.bridge.pane_(identifier))
        size = pane.frame().size
        self.toolbar.setSelectedItemIdentifier_(identifier)
        self.window.setTitle_(next(title for key, title, _ in PANES if key == identifier))
        self.window.setContentView_(NSView.alloc().initWithFrame_(NSMakeRect(0, 0, size.width, size.height)))
        frame = self.window.frameRectForContentRect_(NSMakeRect(0, 0, size.width, size.height))
        top = self.window.frame().origin.y + self.window.frame().size.height
        frame.origin.x, frame.origin.y = self.window.frame().origin.x, top - frame.size.height
        self.window.setFrame_display_animate_(frame, True, animate and self.window.isVisible())
        pane.setFrame_(NSMakeRect(0, 0, size.width, size.height))
        self.window.setContentView_(pane)

    def refresh_permissions(self) -> None:
        def mark(granted: bool) -> str:
            return "✓ Выдано" if granted else "✕ Не выдано"
        self.bridge.update_(json.dumps({"accessibility": mark(permissions.accessibility_granted()), "input_monitoring": mark(permissions.input_monitoring_granted()),
                                        "microphone": "Спросит при записи"}))

    def show(self) -> None:
        self.refresh_permissions()
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    @property
    def visible(self) -> bool:
        return bool(self.window.isVisible())

    def changed(self, key: str, value: str) -> None:
        if key == "autostart":
            (autostart.enable if value.strip().lower() == "true" else autostart.disable)()
            return self.on_change(key)
        try:
            cfg.set_option(self.config, key, value)
        except (KeyError, ValueError):
            return
        if key in RESTART_HOTKEY:
            self.engine.reload_hotkey()
        elif key == "history_size":
            self.engine.resize_history()
        cfg.save(self.config)
        self.on_change(key)

    def action(self, name: str) -> None:
        panes = {"open_accessibility": permissions.ACCESSIBILITY_PANE, "open_input": permissions.INPUT_MONITORING_PANE,
                 "open_microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"}
        if name in panes:
            subprocess.run(["open", panes[name]], check=False)
        elif name == "update":
            self.bridge.update_(json.dumps({"update_status": self.on_update()}))
        elif name == "show_log":
            subprocess.run(["open", "-R", LOG_PATH], check=False)
