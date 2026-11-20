"""Окно настроек: оболочка AppKit с панелью разделов и системные формы SwiftUI из spoke/native."""
from __future__ import annotations

import json
import logging
import os
import subprocess

import objc
from AppKit import (NSApp, NSBackingStoreBuffered, NSImage, NSMakeRect, NSObject, NSToolbar, NSToolbarDisplayModeIconAndLabel,
                    NSToolbarItem, NSView, NSWindow, NSWindowStyleMaskClosable, NSWindowStyleMaskTitled,
                    NSWindowToolbarStylePreference)

from spoke import __version__, autostart, config as cfg, permissions
from spoke.apple_speech import available as apple_speech_available
from spoke.engine import IDLE
from spoke.native_bridge import load_library

LOG_PATH = os.path.expanduser("~/Library/Logs/Spoke.log")
PANES = {"general": ("Основные", "gearshape"), "overlay": ("Плашка и звук", "waveform"), "system": ("Система", "lock.shield")}


class _Controller(NSObject):
    owner = None

    def selectPane_(self, sender):
        self.owner.select(str(sender.itemIdentifier()))

    def toolbarAllowedItemIdentifiers_(self, toolbar):
        return list(PANES)

    def toolbarDefaultItemIdentifiers_(self, toolbar):
        return list(PANES)

    def toolbarSelectableItemIdentifiers_(self, toolbar):
        return list(PANES)

    def toolbar_itemForItemIdentifier_willBeInsertedIntoToolbar_(self, toolbar, identifier, flag):
        title, icon = PANES[identifier]
        item = NSToolbarItem.alloc().initWithItemIdentifier_(identifier)
        item.setLabel_(title)
        item.setImage_(NSImage.imageWithSystemSymbolName_accessibilityDescription_(icon, title))
        item.setTarget_(self)
        item.setAction_("selectPane:")
        return item

    def settingChanged_value_(self, key, value):
        self.owner.changed(str(key), str(value))

    def settingAction_(self, name):
        self.owner.action(str(name))

    def windowDidResignKey_(self, _):
        self.owner.bridge.stopCapture()


class SettingsWindow:
    def __init__(self, config: cfg.Config, engine, on_change=lambda key: None, on_update=lambda: ""):
        self.config, self.engine, self.on_change, self.on_update = config, engine, on_change, on_update
        load_library()
        self.controller = _Controller.alloc().init()
        self.controller.owner = self
        self.bridge = objc.lookUpClass("SpokeSettingsBridge").alloc().initWithConfig_delegate_(json.dumps(self._snapshot()), self.controller)
        self.panes: dict[str, NSView] = {}
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 560, 300), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable, NSBackingStoreBuffered, False)
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self.controller)
        self.toolbar = NSToolbar.alloc().initWithIdentifier_("spoke.preferences")
        self.toolbar.setDelegate_(self.controller)
        self.toolbar.setAllowsUserCustomization_(False)
        self.toolbar.setDisplayMode_(NSToolbarDisplayModeIconAndLabel)
        self.window.setTitlebarAppearsTransparent_(True)
        self.window.setToolbar_(self.toolbar)
        self.window.setToolbarStyle_(NSWindowToolbarStylePreference)
        self.select("general", animate=False)
        self.window.center()

    def _snapshot(self) -> dict:
        c = self.config
        return {"version": __version__, "hotkey_title": c.hotkey_title(), "autostart": c.autostart, "overlay": c.overlay,
                "overlay_bottom_margin": c.overlay_bottom_margin, "sounds": c.sounds, "keep_warm_seconds": c.keep_warm_seconds,
                "check_updates_on_start": c.check_updates_on_start, "apple_available": apple_speech_available(),
                **self._recognition_snapshot()}

    def _recognition_snapshot(self) -> dict:
        return {"recognition_backend": self.engine.pending_backend or self.engine.recognition_backend,
                "backend_busy": self.engine.state != IDLE, "backend_loading": self.engine.pending_backend is not None}

    def select(self, identifier: str, animate: bool = True) -> None:
        self.bridge.stopCapture()
        self.toolbar.setSelectedItemIdentifier_(identifier)
        self.window.setTitle_(PANES[identifier][0])
        if identifier not in self.panes:
            self.panes[identifier] = self.bridge.pane_(identifier)
        pane = self.panes[identifier]
        size = pane.fittingSize()
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
                                        "microphone": "Спросит при записи", **self._recognition_snapshot()}))

    def show(self) -> None:
        self.refresh_permissions()
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    @property
    def visible(self) -> bool:
        return bool(self.window.isVisible())

    def changed(self, key: str, value: str) -> None:
        if key == "recognition_backend":
            self.engine.switch_backend(value, cfg.save)
            self.bridge.update_(json.dumps(self._recognition_snapshot()))
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
        self.on_change(key)

    def action(self, name: str) -> None:
        panes = {"open_accessibility": permissions.ACCESSIBILITY_PANE, "open_input": permissions.INPUT_MONITORING_PANE,
                 "open_microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"}
        if name in ("capture_start", "capture_stop"):
            self.engine.capturing_shortcut = name == "capture_start"
        elif name in panes:
            subprocess.run(["open", panes[name]], check=False)
        elif name == "update":
            self.bridge.update_(json.dumps({"update_status": self.on_update()}))
        elif name == "show_log":
            subprocess.run(["open", "-R", LOG_PATH], check=False)
