"""Окно настроек в системном стиле: значки-разделы в панели инструментов, выровненные формы, окно подстраивается под раздел."""
from __future__ import annotations

import os
import subprocess

from AppKit import (NSApp, NSBackingStoreBuffered, NSButton, NSColor, NSFont, NSGridCellPlacementLeading, NSGridCellPlacementTop, NSGridCellPlacementTrailing,
                    NSGridRowAlignmentFirstBaseline, NSGridView, NSImage, NSLayoutAttributeLeading, NSMakeRect, NSObject, NSPopUpButton, NSScrollView, NSSlider,
                    NSStackView, NSStepper, NSTextField, NSTextView, NSToolbar, NSToolbarItem, NSView, NSWindow, NSWindowStyleMaskClosable,
                    NSWindowStyleMaskTitled, NSWindowToolbarStylePreference)

from spoke import __version__, autostart, config as cfg, permissions

LOG_PATH = os.path.expanduser("~/Library/Logs/Spoke.log")
PADDING = 26.0
PANES = (("general", "Основные", "gearshape"), ("overlay", "Плашка и звук", "waveform"), ("vocabulary", "Словарь", "text.book.closed"),
         ("system", "Система", "lock.shield"))
UNITS = {"hold_ms": "мс", "double_tap_ms": "мс", "overlay_bottom_margin": "пт"}


class _Controller(NSObject):
    owner = None

    def changed_(self, sender):
        self.owner.changed(str(sender.identifier()), sender)

    def action_(self, sender):
        self.owner.action(str(sender.identifier()))

    def pane_(self, sender):
        self.owner.select(str(sender.itemIdentifier()))

    def textDidChange_(self, notification):
        self.owner.vocabulary_changed()

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


def _label(text: str, secondary: bool = False, size: float = 13) -> NSTextField:
    label = NSTextField.labelWithString_(text)
    label.setFont_(NSFont.systemFontOfSize_(size))
    if secondary:
        label.setTextColor_(NSColor.secondaryLabelColor())
    return label


def _stack(views, vertical: bool = False, spacing: float = 8) -> NSStackView:
    stack = NSStackView.stackViewWithViews_(views)
    stack.setOrientation_(1 if vertical else 0)
    stack.setSpacing_(spacing)
    if vertical:
        stack.setAlignment_(NSLayoutAttributeLeading)
    return stack


class SettingsWindow:
    def __init__(self, config: cfg.Config, engine, on_change=lambda key: None, on_update=lambda: ""):
        self.config, self.engine, self.on_change, self.on_update = config, engine, on_change, on_update
        self.controller = _Controller.alloc().init()
        self.controller.owner = self
        self.values: dict[str, NSTextField] = {}
        self.statuses: dict[str, NSTextField] = {}
        self.update_status = _label("", True, 11)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 480, 300), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable, NSBackingStoreBuffered, False)
        self.window.setReleasedWhenClosed_(False)
        toolbar = NSToolbar.alloc().initWithIdentifier_("spoke.settings")
        toolbar.setDelegate_(self.controller)
        toolbar.setAllowsUserCustomization_(False)
        self.toolbar = toolbar
        self.window.setToolbar_(toolbar)
        self.window.setToolbarStyle_(NSWindowToolbarStylePreference)
        self.panes = {"general": self._general(), "overlay": self._overlay(), "vocabulary": self._vocabulary(), "system": self._system()}
        self.window.center()
        self.select("general", animate=False)

    def select(self, identifier: str, animate: bool = True) -> None:
        pane = self.panes[identifier]
        size = pane.frame().size
        self.toolbar.setSelectedItemIdentifier_(identifier)
        self.window.setTitle_(next(title for key, title, _ in PANES if key == identifier))
        self.window.setContentView_(NSView.alloc().initWithFrame_(NSMakeRect(0, 0, size.width, size.height)))
        frame = self.window.frameRectForContentRect_(NSMakeRect(0, 0, size.width, size.height))
        top = self.window.frame().origin.y + self.window.frame().size.height
        frame.origin.x, frame.origin.y = self.window.frame().origin.x, top - frame.size.height
        self.window.setFrame_display_animate_(frame, True, animate and self.window.isVisible())
        self.window.setContentView_(pane)

    def _pane(self, rows: list[list], tops: dict[int, float] | None = None, aligned_top: tuple[int, ...] = ()) -> NSView:
        grid = NSGridView.gridViewWithViews_(rows)
        grid.setRowSpacing_(12)
        grid.setColumnSpacing_(12)
        grid.columnAtIndex_(0).setXPlacement_(NSGridCellPlacementTrailing)
        grid.columnAtIndex_(1).setXPlacement_(NSGridCellPlacementLeading)
        grid.setRowAlignment_(NSGridRowAlignmentFirstBaseline)
        for index, padding in (tops or {}).items():
            grid.rowAtIndex_(index).setTopPadding_(padding)
        for index in aligned_top:
            grid.rowAtIndex_(index).setYPlacement_(NSGridCellPlacementTop)
        size = grid.fittingSize()
        pane = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, size.width + 2 * PADDING, size.height + 2 * PADDING))
        grid.setFrame_(NSMakeRect(PADDING, PADDING, size.width, size.height))
        pane.addSubview_(grid)
        return pane

    def _wire(self, control, key: str):
        control.setIdentifier_(key)
        control.setTarget_(self.controller)
        control.setAction_("changed:")
        return control

    def _popup(self, key: str, titles: list[str], selected: int) -> NSPopUpButton:
        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 200, 26), False)
        popup.addItemsWithTitles_(titles)
        popup.selectItemAtIndex_(selected)
        return self._wire(popup, key)

    def _check(self, key: str, title: str, on: bool) -> NSButton:
        box = NSButton.checkboxWithTitle_target_action_(title, self.controller, "changed:")
        box.setState_(int(on))
        box.setIdentifier_(key)
        return box

    def _slider(self, key: str, low: float, high: float, value: float) -> NSStackView:
        slider = NSSlider.sliderWithValue_minValue_maxValue_target_action_(value, low, high, self.controller, "changed:")
        slider.setIdentifier_(key)
        slider.widthAnchor().constraintEqualToConstant_(190).setActive_(True)
        shown = _label(f"{int(value)} {UNITS[key]}")
        shown.widthAnchor().constraintEqualToConstant_(56).setActive_(True)
        self.values[key] = shown
        return _stack([slider, shown])

    def _stepper(self, key: str, low: int, high: int, value: int, unit: str) -> NSStackView:
        stepper = NSStepper.alloc().initWithFrame_(NSMakeRect(0, 0, 19, 27))
        stepper.setMinValue_(low)
        stepper.setMaxValue_(high)
        stepper.setIntValue_(value)
        self._wire(stepper, key)
        shown = _label(str(value))
        shown.widthAnchor().constraintEqualToConstant_(30).setActive_(True)
        self.values[key] = shown
        return _stack([shown, stepper, _label(unit, True)])

    def _button(self, key: str, title: str) -> NSButton:
        button = NSButton.buttonWithTitle_target_action_(title, self.controller, "action:")
        button.setIdentifier_(key)
        return button

    def _with_hint(self, control, hint: str) -> NSStackView:
        return _stack([control, _label(hint, True, 11)], vertical=True, spacing=4)

    def _general(self) -> NSView:
        c = self.config
        rows = [
            [_label("Клавиша диктовки:"), self._popup("hotkey", list(cfg.HOTKEY_NAMES.values()), list(cfg.HOTKEY_NAMES).index(c.hotkey))],
            [_label("Удержание до записи:"), self._slider("hold_ms", 100, 800, c.hold_ms)],
            [_label("Двойное нажатие:"), self._with_hint(self._slider("double_tap_ms", 200, 800, c.double_tap_ms), "Два быстрых нажатия включают постоянную запись.")],
            [_label("Язык речи:"), self._popup("language", list(cfg.LANGUAGES.values()), list(cfg.LANGUAGES).index(c.language))],
            [_label("История:"), self._stepper("history_size", 1, 50, c.history_size, "последних диктовок (только в памяти)")],
            [_label("Вставка:"), self._check("auto_space", "Ставить пробел между подряд идущими вставками", c.auto_space)],
            [_label("Запуск:"), self._check("autostart", "Запускать при входе в систему", autostart.enabled())],
        ]
        return self._pane(rows, tops={3: 20, 5: 20})

    def _overlay(self) -> NSView:
        c = self.config
        rows = [
            [_label("Плашка:"), self._check("overlay", "Показывать плашку записи внизу экрана", c.overlay)],
            [_label("Высота над Dock:"), self._with_hint(self._slider("overlay_bottom_margin", 0, 80, c.overlay_bottom_margin), "Если Dock скрыт, отсчёт идёт от низа экрана.")],
            [_label("Звук:"), self._check("sounds", "Щелчки в начале и конце записи", c.sounds)],
            [_label("Распознавание:"), self._with_hint(self._check("streaming", "Распознавать, пока вы говорите", c.streaming),
                                                       "Быстрее на длинных диктовках; числа иногда пишутся иначе.")],
        ]
        return self._pane(rows, tops={2: 20, 3: 20})

    def _vocabulary(self) -> NSView:
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 360, 200))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(2)
        text = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 340, 200))
        text.setFont_(NSFont.systemFontOfSize_(13))
        text.setVerticallyResizable_(True)
        text.setAutoresizingMask_(2)
        text.setString_("\n".join(self.config.vocabulary))
        text.setDelegate_(self.controller)
        scroll.setDocumentView_(text)
        scroll.widthAnchor().constraintEqualToConstant_(360).setActive_(True)
        scroll.heightAnchor().constraintEqualToConstant_(200).setActive_(True)
        self.vocabulary_view = text
        hint = "По одному в строке, например Пётр или Kubernetes."
        return self._pane([[_label("Имена и термины:"), self._with_hint(scroll, hint)]], aligned_top=(0,))

    def _status_row(self, key: str, button_key: str) -> NSStackView:
        status = _label("")
        status.widthAnchor().constraintEqualToConstant_(150).setActive_(True)
        self.statuses[key] = status
        return _stack([status, self._button(button_key, "Открыть настройки…")])

    def _system(self) -> NSView:
        c = self.config
        update = _stack([self._button("update", "Проверить обновления…"), self.update_status])
        rows = [
            [_label("Универсальный доступ:"), self._status_row("accessibility", "open_accessibility")],
            [_label("Мониторинг ввода:"), self._status_row("input", "open_input")],
            [_label("Микрофон:"), self._status_row("microphone", "open_microphone")],
            [_label("Модель в памяти:"), self._with_hint(self._stepper("keep_warm_seconds", 0, 300, c.keep_warm_seconds, "с между поддержанием (0 — выключено)"),
                                                         "Без этого первая диктовка после паузы идёт 5–15 с.")],
            [_label("Журнал:"), self._button("show_log", "Показать в Finder")],
            [_label(f"Версия {__version__}:"), update],
            [_label(""), self._check("check_updates_on_start", "Проверять обновления при запуске", c.check_updates_on_start)],
        ]
        pane = self._pane(rows, tops={3: 20, 5: 20, 6: 6})
        self.refresh_permissions()
        return pane

    def refresh_permissions(self) -> None:
        if self.statuses:
            self.statuses["accessibility"].setStringValue_("✓ Выдано" if permissions.accessibility_granted() else "✕ Не выдано")
            self.statuses["input"].setStringValue_("✓ Выдано" if permissions.input_monitoring_granted() else "✕ Не выдано")
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
        elif key in UNITS:
            value = int(round(sender.doubleValue()))
            setattr(c, key, float(value) if key == "overlay_bottom_margin" else value)
            self.values[key].setStringValue_(f"{value} {UNITS[key]}")
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
        elif key in ("auto_space", "overlay", "sounds", "streaming", "check_updates_on_start"):
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
        elif key == "update":
            self.update_status.setStringValue_(self.on_update())
        elif key == "show_log":
            subprocess.run(["open", "-R", LOG_PATH], check=False)
