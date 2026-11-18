"""Окно настроек: основные, плашка и звук, словарь имён, система. Изменения применяются сразу и сохраняются в config.json."""
from __future__ import annotations

import os
import subprocess

import objc
from AppKit import (NSApp, NSBackingStoreBuffered, NSButton, NSColor, NSFont, NSMakeRect, NSObject, NSPopUpButton, NSScrollView, NSSlider, NSStepper,
                    NSTabView, NSTabViewItem, NSTextField, NSTextView, NSView, NSWindow, NSWindowStyleMaskClosable, NSWindowStyleMaskMiniaturizable,
                    NSWindowStyleMaskTitled)

from spoke import __version__, autostart, config as cfg, permissions

WIDTH, HEIGHT = 560, 450
CONTENT_HEIGHT = 380
LABEL_X, CONTROL_X, ROW = 20.0, 210.0, 38.0
LOG_PATH = os.path.expanduser("~/Library/Logs/Spoke.log")


class _Controller(NSObject):
    owner = None

    def changed_(self, sender):
        self.owner.changed(str(sender.identifier()), sender)

    def textDidChange_(self, notification):
        self.owner.vocabulary_changed()

    def action_(self, sender):
        self.owner.action(str(sender.identifier()))


class SettingsWindow:
    def __init__(self, config: cfg.Config, engine, on_change=lambda key: None):
        self.config, self.engine, self.on_change = config, engine, on_change
        self.controller = _Controller.alloc().init()
        self.controller.owner = self
        self.values: dict[str, NSTextField] = {}
        self.statuses: dict[str, NSTextField] = {}
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIDTH, HEIGHT), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable, NSBackingStoreBuffered, False)
        self.window.setTitle_("Настройки Spoke")
        self.window.setReleasedWhenClosed_(False)
        self.window.center()
        tabs = NSTabView.alloc().initWithFrame_(NSMakeRect(12, 12, WIDTH - 24, HEIGHT - 28))
        for title, build in (("Основные", self._general), ("Плашка и звук", self._overlay), ("Словарь", self._vocabulary), ("Система", self._system)):
            item = NSTabViewItem.alloc().initWithIdentifier_(title)
            item.setLabel_(title)
            view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH - 44, CONTENT_HEIGHT))
            build(view)
            item.setView_(view)
            tabs.addTabViewItem_(item)
        self.window.contentView().addSubview_(tabs)

    def _y(self, row: int) -> float:
        return CONTENT_HEIGHT - 44 - row * ROW

    def _label(self, view, row: int, text: str, width: float = 180) -> None:
        label = NSTextField.labelWithString_(text)
        label.setFrame_(NSMakeRect(LABEL_X, self._y(row), width, 20))
        view.addSubview_(label)

    def _hint(self, view, row: int, text: str) -> None:
        hint = NSTextField.labelWithString_(text)
        hint.setFont_(NSFont.systemFontOfSize_(11))
        hint.setTextColor_(NSColor.secondaryLabelColor())
        hint.setFrame_(NSMakeRect(CONTROL_X, self._y(row) - 17, 290, 16))
        view.addSubview_(hint)

    def _wire(self, control, key: str):
        control.setIdentifier_(key)
        control.setTarget_(self.controller)
        control.setAction_("changed:")
        return control

    def _popup(self, view, row: int, key: str, titles: list[str], selected: int) -> None:
        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(CONTROL_X, self._y(row) - 3, 220, 26), False)
        popup.addItemsWithTitles_(titles)
        popup.selectItemAtIndex_(selected)
        view.addSubview_(self._wire(popup, key))

    def _check(self, view, row: int, key: str, title: str, on: bool) -> None:
        box = NSButton.checkboxWithTitle_target_action_(title, self.controller, "changed:")
        box.setFrame_(NSMakeRect(CONTROL_X, self._y(row) - 2, 300, 22))
        box.setState_(int(on))
        box.setIdentifier_(key)
        view.addSubview_(box)

    def _slider(self, view, row: int, key: str, low: float, high: float, value: float, unit: str) -> None:
        slider = NSSlider.sliderWithValue_minValue_maxValue_target_action_(value, low, high, self.controller, "changed:")
        slider.setIdentifier_(key)
        slider.setFrame_(NSMakeRect(CONTROL_X, self._y(row) - 2, 190, 22))
        view.addSubview_(slider)
        shown = NSTextField.labelWithString_(f"{int(value)} {unit}")
        shown.setFrame_(NSMakeRect(CONTROL_X + 200, self._y(row), 90, 20))
        view.addSubview_(shown)
        self.values[key] = shown
        self.values[key + "_unit"] = unit

    def _stepper(self, view, row: int, key: str, low: int, high: int, value: int, unit: str) -> None:
        stepper = NSStepper.alloc().initWithFrame_(NSMakeRect(CONTROL_X + 60, self._y(row) - 3, 19, 27))
        stepper.setMinValue_(low)
        stepper.setMaxValue_(high)
        stepper.setIntValue_(value)
        view.addSubview_(self._wire(stepper, key))
        shown = NSTextField.labelWithString_(f"{value}")
        shown.setFrame_(NSMakeRect(CONTROL_X, self._y(row), 54, 20))
        view.addSubview_(shown)
        unit_label = NSTextField.labelWithString_(unit)
        unit_label.setFrame_(NSMakeRect(CONTROL_X + 86, self._y(row), 200, 20))
        view.addSubview_(unit_label)
        self.values[key] = shown

    def _general(self, view) -> None:
        c = self.config
        self._label(view, 0, "Клавиша диктовки")
        self._popup(view, 0, "hotkey", list(cfg.HOTKEY_NAMES.values()), list(cfg.HOTKEY_NAMES).index(c.hotkey))
        self._label(view, 1, "Удержание до записи")
        self._slider(view, 1, "hold_ms", 100, 800, c.hold_ms, "мс")
        self._label(view, 2, "Двойное нажатие")
        self._slider(view, 2, "double_tap_ms", 200, 800, c.double_tap_ms, "мс")
        self._hint(view, 2, "Два быстрых нажатия включают постоянную запись.")
        self._label(view, 3.4, "Язык речи")
        self._popup(view, 3.4, "language", list(cfg.LANGUAGES.values()), list(cfg.LANGUAGES).index(c.language))
        self._label(view, 4.4, "Помнить диктовок")
        self._stepper(view, 4.4, "history_size", 1, 50, c.history_size, "последних, только в памяти")
        self._label(view, 5.4, "Между вставками")
        self._check(view, 5.4, "auto_space", "Ставить пробел, чтобы текст не слипался", c.auto_space)
        self._label(view, 6.4, "Запуск")
        self._check(view, 6.4, "autostart", "Запускать при входе в систему", autostart.enabled())

    def _overlay(self, view) -> None:
        c = self.config
        self._label(view, 0, "Плашка записи")
        self._check(view, 0, "overlay", "Показывать плашку внизу экрана", c.overlay)
        self._label(view, 1, "Высота над Dock")
        self._slider(view, 1, "overlay_bottom_margin", 0, 80, c.overlay_bottom_margin, "пт")
        self._hint(view, 1, "Если Dock скрыт, отсчёт идёт от низа экрана.")
        self._label(view, 2.4, "Звук")
        self._check(view, 2.4, "sounds", "Щелчки в начале и конце записи", c.sounds)
        self._label(view, 3.4, "Распознавание")
        self._check(view, 3.4, "streaming", "Распознавать, пока вы говорите", c.streaming)
        self._hint(view, 3.4, "Быстрее на длинных; числа иногда пишутся иначе.")

    def _vocabulary(self, view) -> None:
        self._label(view, 0, "Имена и термины, по одному в строке", 440)
        self._hint_line = NSTextField.labelWithString_("Whisper будет писать их так, как указано (например, Пётр, Kubernetes, Allure).")
        self._hint_line.setFont_(NSFont.systemFontOfSize_(11))
        self._hint_line.setFrame_(NSMakeRect(LABEL_X, self._y(0) - 20, 480, 16))
        view.addSubview_(self._hint_line)
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(LABEL_X, 20, WIDTH - 84, CONTENT_HEIGHT - 110))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(2)
        text = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH - 100, CONTENT_HEIGHT - 110))
        text.setFont_(NSFont.systemFontOfSize_(13))
        text.setVerticallyResizable_(True)
        text.setAutoresizingMask_(2)
        text.setString_("\n".join(self.config.vocabulary))
        text.setDelegate_(self.controller)
        scroll.setDocumentView_(text)
        view.addSubview_(scroll)
        self.vocabulary_view = text

    def _system(self, view) -> None:
        c = self.config
        for row, (key, title) in enumerate((("accessibility", "Универсальный доступ"), ("input", "Мониторинг ввода"), ("microphone", "Микрофон"))):
            self._label(view, row, title)
            status = NSTextField.labelWithString_("")
            status.setFrame_(NSMakeRect(CONTROL_X, self._y(row), 150, 20))
            view.addSubview_(status)
            self.statuses[key] = status
            button = NSButton.buttonWithTitle_target_action_("Открыть настройки", self.controller, "action:")
            button.setFrame_(NSMakeRect(CONTROL_X + 150, self._y(row) - 5, 150, 28))
            button.setIdentifier_("open_" + key)
            view.addSubview_(button)
        self._label(view, 3.4, "Модель в памяти")
        self._stepper(view, 3.4, "keep_warm_seconds", 0, 300, c.keep_warm_seconds, "с (0 — выключено)")
        self._hint(view, 3.4, "Без этого первая диктовка после паузы идёт 5–15 с.")
        self._label(view, 4.8, "Журнал")
        log = NSButton.buttonWithTitle_target_action_("Показать в Finder", self.controller, "action:")
        log.setFrame_(NSMakeRect(CONTROL_X, self._y(4.8) - 5, 150, 28))
        log.setIdentifier_("show_log")
        view.addSubview_(log)
        self._label(view, 6.0, "Версия " + __version__)
        self.refresh_permissions()

    def refresh_permissions(self) -> None:
        def mark(ok: bool | None) -> str:
            return "Не проверяется" if ok is None else ("✓ Выдано" if ok else "✕ Не выдано")
        if self.statuses:
            self.statuses["accessibility"].setStringValue_(mark(permissions.accessibility_granted()))
            self.statuses["input"].setStringValue_(mark(permissions.input_monitoring_granted()))
            self.statuses["microphone"].setStringValue_("Спросит при записи")

    def show(self) -> None:
        self.refresh_permissions()
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    @property
    def visible(self) -> bool:
        return bool(self.window.isVisible())

    def changed(self, key: str, sender) -> None:
        c = self.config
        if key == "hotkey":
            c.hotkey = list(cfg.HOTKEY_NAMES)[sender.indexOfSelectedItem()]
            self.engine.reload_hotkey()
        elif key == "language":
            c.language = list(cfg.LANGUAGES)[sender.indexOfSelectedItem()]
        elif key in ("hold_ms", "double_tap_ms", "overlay_bottom_margin"):
            value = int(round(sender.doubleValue()))
            setattr(c, key, float(value) if key == "overlay_bottom_margin" else value)
            self.values[key].setStringValue_(f"{value} {self.values[key + '_unit']}")
            if key != "overlay_bottom_margin":
                self.engine.reload_hotkey()
        elif key in ("history_size", "keep_warm_seconds"):
            value = int(sender.intValue())
            setattr(c, key, value)
            self.values[key].setStringValue_(str(value))
            if key == "history_size":
                self.engine.resize_history()
        elif key == "autostart":
            (autostart.enable if sender.state() else autostart.disable)()
            return self.on_change(key)
        elif key in ("auto_space", "overlay", "sounds", "streaming"):
            setattr(c, key, bool(sender.state()))
        cfg.save(c)
        self.on_change(key)

    def vocabulary_changed(self) -> None:
        lines = [line.strip() for line in str(self.vocabulary_view.string()).splitlines()]
        self.config.vocabulary = [line for line in lines if line]
        cfg.save(self.config)

    def action(self, key: str) -> None:
        if key == "open_accessibility":
            subprocess.run(["open", permissions.ACCESSIBILITY_PANE], check=False)
        elif key == "open_input":
            subprocess.run(["open", permissions.INPUT_MONITORING_PANE], check=False)
        elif key == "open_microphone":
            subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"], check=False)
        elif key == "show_log":
            subprocess.run(["open", "-R", LOG_PATH], check=False)
