"""Иконка в строке меню: состояние, история, окно настроек."""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import rumps
from AppKit import NSImage
from Foundation import NSActivityLatencyCritical, NSActivityUserInitiatedAllowingIdleSystemSleep, NSProcessInfo

from spoke import __version__, config as cfg, inject, permissions, updater
from spoke.settings import SettingsWindow
from spoke.overlay import RECORD, WORK, Overlay
from spoke.engine import IDLE, LOADING, RECORDING, WORKING, Engine

LOCKED_STATUS = "Постоянная запись: Ctrl — завершить, Esc — отмена"
ASSETS = Path(__file__).resolve().parent / "assets"
ICONS = {LOADING: ("menu_loading.png", True), IDLE: ("menu_idle.png", True), RECORDING: ("menu_recording.png", False), WORKING: ("menu_working.png", True)}
ICON_POINTS = 22.0
STATUS = {LOADING: "Загружаю модель…", IDLE: "Готов: зажмите клавишу и говорите", RECORDING: "Слушаю…", WORKING: "Распознаю…"}


class SpokeApp(rumps.App):
    def __init__(self, config: cfg.Config):
        super().__init__("Spoke", title=None, icon=str(ASSETS / ICONS[LOADING][0]), template=True, quit_button=None)
        self._shown_icon = None
        self.config = config
        self.engine = Engine(config, notify=lambda title, text: rumps.notification(title, "", text))
        self.status_item = rumps.MenuItem(STATUS[LOADING])
        self.paste_last_item = rumps.MenuItem("Вставить последний текст", callback=self._paste_last)
        self.copy_last_item = rumps.MenuItem("Скопировать последний текст", callback=self._copy_last)
        self.history_menu = rumps.MenuItem("История")
        self._history_shown: tuple | None = None
        self._refresh_history()
        self.settings = None
        self.update_item = rumps.MenuItem("Проверить обновления…", callback=self.check_updates)
        self._available: updater.Release | None = None
        self.menu = [self.status_item, None, self.paste_last_item, self.copy_last_item, self.history_menu, None,
                     rumps.MenuItem("Настройки…", callback=self._open_settings, key=","), self.update_item, None,
                     rumps.MenuItem("Выйти", callback=rumps.quit_application)]
        self._timer = rumps.Timer(self._refresh, 0.25)
        self._timer.start()
        self.overlay = Overlay(config.overlay_bottom_margin, self.engine.cancel_locked, self.engine.finish_locked) if config.overlay else None
        self._overlay_timer = rumps.Timer(self._animate, 0.05)
        self._overlay_timer.start()
        self._permissions_ok = permissions.all_granted()
        if not self._permissions_ok:
            permissions.accessibility_granted(prompt=True)
            permissions.input_monitoring_granted(prompt=True)
        self.engine.start_listening()
        if config.check_updates_on_start:
            threading.Thread(target=self._check_in_background, daemon=True).start()

    def _check_in_background(self) -> None:
        time.sleep(20)
        try:
            self._available = updater.check(self.config.token())
        except Exception:
            logging.getLogger("spoke").exception("Не удалось проверить обновления")

    def check_updates(self, _=None) -> str:
        try:
            release = updater.check(self.config.token())
        except updater.UpdateError as error:
            return self._say(str(error))
        if release is None:
            self._available = None
            return self._say(f"Установлена последняя версия ({__version__})")
        notes = updater.release_notes(release.notes)[:600] or "Установить обновление?"
        if not rumps.alert(title=f"Доступно обновление {release.version}", message=notes, ok="Обновить", cancel="Позже"):
            return "Обновление отложено"
        try:
            updater.apply(release, self.config.token())
        except updater.UpdateError as error:
            return self._say(str(error))
        if updater.restart():
            rumps.notification("Spoke", "Обновление установлено", "Перезапускаюсь…")
            rumps.quit_application()
            return "Обновление установлено, перезапуск…"
        return self._say("Обновление установлено, перезапустите Spoke")

    def _say(self, message: str) -> str:
        rumps.notification("Spoke", "", message)
        return message

    def _check_permissions(self) -> None:
        granted = permissions.all_granted()
        if granted and not self._permissions_ok:
            self.engine.restart_listening()
        self._permissions_ok = granted

    def _open_settings(self, _) -> None:
        if self.settings is None:
            self.settings = SettingsWindow(self.config, self.engine, self._settings_changed, self.check_updates)
        self.settings.show()

    def _settings_changed(self, key: str) -> None:
        if key in ("overlay", "overlay_bottom_margin"):
            self.apply_overlay()

    def apply_overlay(self) -> None:
        if self.config.overlay and self.overlay is None:
            self.overlay = Overlay(self.config.overlay_bottom_margin, self.engine.cancel_locked, self.engine.finish_locked)
        elif not self.config.overlay and self.overlay is not None:
            self.overlay.update(None)
            self.overlay = None
        if self.overlay is not None:
            self.overlay.bottom_margin = self.config.overlay_bottom_margin

    def _open_permissions(self, _) -> None:
        permissions.open_settings()

    def _show_icon(self, state: str) -> None:
        if state == self._shown_icon:
            return
        name, template = ICONS.get(state, ICONS[IDLE])
        image = NSImage.alloc().initWithContentsOfFile_(str(ASSETS / name))
        if image is None:
            self.title = "S"
            return
        image.setSize_((ICON_POINTS, ICON_POINTS))
        image.setTemplate_(template)
        self._nsapp.nsstatusitem.setImage_(image)
        self._nsapp.nsstatusitem.setTitle_(None)
        self._shown_icon = state

    def _animate(self, _) -> None:
        if self.overlay is None:
            return
        state = self.engine.state
        if state == RECORDING:
            self.overlay.update(RECORD, getattr(self.engine.recorder, "level", 0.0), self.engine.locked)
        elif state == WORKING:
            self.overlay.update(WORK)
        else:
            self.overlay.update(None)

    def _refresh(self, _) -> None:
        state = self.engine.state
        self._show_icon(state)
        self._check_permissions()
        self.status_item.title = LOCKED_STATUS if state == RECORDING and self.engine.locked else STATUS.get(state, "")
        if not self._permissions_ok:
            self.status_item.title = "⚠️ Нужны разрешения: нажмите, чтобы открыть настройки"
            self.status_item.set_callback(self._open_permissions)
        elif self.engine.last_error:
            self.status_item.title = "⚠️ " + self.engine.last_error[:60]
            self.status_item.set_callback(None)
        else:
            self.status_item.set_callback(None)
        self.update_item.title = f"Обновить до {self._available.version}…" if self._available else "Проверить обновления…"
        self._refresh_history()
        if self.settings is not None and self.settings.visible:
            self.settings.refresh_permissions()

    def _refresh_history(self) -> None:
        history = tuple(self.engine.history)
        if history == self._history_shown:
            return
        self._history_shown = history
        has = bool(history)
        self.paste_last_item.set_callback(self._paste_last if has else None)
        self.copy_last_item.set_callback(self._copy_last if has else None)
        if self.history_menu.keys():
            self.history_menu.clear()
        for index, text in enumerate(history):
            line = " ".join(text.split())
            self.history_menu.add(rumps.MenuItem(f"{index + 1}. {line[:60]}{'…' if len(line) > 60 else ''}", callback=self._paste_history(index)))
        if not has:
            self.history_menu.add(rumps.MenuItem("Пока пусто"))

    def _paste_last(self, _) -> None:
        self.engine.paste_from_history(0)

    def _copy_last(self, _) -> None:
        if self.engine.history:
            inject.set_clipboard(self.engine.history[0])

    def _paste_history(self, index: int):
        def handler(_):
            self.engine.paste_from_history(index)
        return handler

_ACTIVITY = None


def run() -> None:
    global _ACTIVITY
    _ACTIVITY = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
        NSActivityUserInitiatedAllowingIdleSystemSleep | NSActivityLatencyCritical, "Spoke: мгновенная диктовка без App Nap")
    logging.basicConfig(filename=os.path.expanduser("~/Library/Logs/Spoke.log"), level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = cfg.load()
    if not cfg.CONFIG_PATH.exists():
        cfg.save(config)
    SpokeApp(config).run()
