"""Иконка в строке меню: состояние, история, окно настроек."""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from pathlib import Path
from queue import Empty, SimpleQueue

import rumps
from PyObjCTools import AppHelper
from AppKit import NSApp, NSImage, NSWorkspace
from Foundation import NSActivityUserInitiatedAllowingIdleSystemSleep, NSProcessInfo, NSURL

from spoke import __version__, autostart, config as cfg, inject, permissions, updater
from spoke.settings import SettingsWindow
from spoke.overlay import FIELD_FORMAT, FIELD_FORMATTED, RECORD, WORK, Overlay
from spoke.engine import IDLE, LOADING, RECORDING, WORKING, Engine

ASSETS = Path(__file__).resolve().parent / "assets"
ICONS = {LOADING: ("menu_loading.png", True), IDLE: ("menu_idle.png", True), RECORDING: ("menu_recording.png", False), WORKING: ("menu_working.png", True)}
ICON_POINTS = 22.0
FIELD_CONFIRMATION_SECONDS = 1.35
STATUS = {LOADING: "Загружаю модель…", IDLE: "Готов: зажмите клавишу и говорите", RECORDING: "Слушаю…", WORKING: "Распознаю…"}


class SpokeApp(rumps.App):
    def __init__(self, config: cfg.Config):
        super().__init__("Spoke", title=None, icon=str(ASSETS / ICONS[LOADING][0]), template=True, quit_button=None)
        self._shown_icon = None
        self._preview_started = 0.0
        self._preview_mode = RECORD
        self.config = config
        self.engine = Engine(config, notify=lambda title, text: rumps.notification(title, "", text), history_path=cfg.HISTORY_FILE, recovery_file=cfg.RECOVERY_FILE)
        self.status_item = rumps.MenuItem(STATUS[LOADING])
        self.paste_last_item = rumps.MenuItem("Вставить последний текст", callback=self._paste_last)
        self.copy_last_item = rumps.MenuItem("Скопировать последний текст", callback=self._copy_last)
        self.history_menu = rumps.MenuItem("История")
        self._history_shown: tuple | None = None
        self._refresh_history()
        self.settings = None
        self.update_item = rumps.MenuItem("Проверить обновления…", callback=self.check_updates)
        self._checking_update = False
        self._update_results = SimpleQueue()
        self._available: updater.Release | None = None
        self.menu = [self.status_item, None, self.paste_last_item, self.copy_last_item, self.history_menu, None,
                     rumps.MenuItem("Настройки…", callback=self._open_settings, key=","), self.update_item, None,
                     rumps.MenuItem("Выйти", callback=rumps.quit_application)]
        self._timer = rumps.Timer(self._refresh, 1.0)
        self._timer.start()
        self.overlay = None
        self.apply_overlay()
        self._overlay_timer = rumps.Timer(self._animate, 0.025)
        self._activity = None
        self._last_permissions = 0.0
        self._pending_update = None
        self.engine.on_state_change = lambda: AppHelper.callAfter(self._sync_animation)
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

    def _update_status(self, message: str, checking: bool = False) -> None:
        self._checking_update = checking
        self.update_item.title = "Проверяю обновления…" if checking else "Проверить обновления…"
        if self.settings is not None:
            self.settings.update_status(message, checking)

    def check_updates(self, _=None) -> None:
        if self._checking_update:
            return
        self._update_status("Проверяю обновления…", checking=True)
        threading.Thread(target=self._check_updates_requested, daemon=True).start()

    def _check_updates_requested(self) -> None:
        try:
            release = updater.check(self.config.token())
        except updater.UpdateError as error:
            self._update_results.put((None, str(error)))
        except Exception:
            logging.getLogger("spoke").exception("Не удалось проверить обновления")
            self._update_results.put((None, "Не удалось прочитать данные последнего релиза."))
        else:
            self._update_results.put((release, ""))

    def _show_update_result(self, release, error: str = "") -> None:
        NSApp.activateIgnoringOtherApps_(True)
        if error:
            self._update_status(error)
            rumps.alert(title="Не удалось проверить обновления", message=error, ok="ОК")
            return
        if release is None:
            self._available = None
            message = f"Установлена актуальная версия Spoke {__version__}."
            self._update_status(message)
            rumps.alert(title="Обновлений нет", message=message, ok="ОК")
            return
        self._available = release
        self._update_status(f"Доступен Spoke {release.version}.")
        notes = updater.release_notes(release.notes)[:600] or "Установить обновление?"
        if updater.uses_signed_distribution():
            if rumps.alert(title=f"Доступно обновление {release.version}", message=notes,
                           ok="Открыть страницу релиза", cancel="Позже"):
                NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(release.page))
            return
        if not rumps.alert(title=f"Доступно обновление {release.version}", message=notes, ok="Обновить", cancel="Позже"):
            return
        self._update_status("Скачиваю обновление…", checking=True)
        threading.Thread(target=self._prepare_update, args=(release,), daemon=True).start()

    def _prepare_update(self, release):
        try:
            staged = updater.prepare(release, self.config.token())
        except Exception as error:
            AppHelper.callAfter(self._show_update_result, None, str(error))
        else:
            AppHelper.callAfter(self._queue_install, staged)

    def _queue_install(self, staged):
        self._pending_update = staged
        self._install_when_idle()

    def _install_when_idle(self):
        if self._pending_update is None or not self.engine.reserve_update():
            return
        staged, self._pending_update = self._pending_update, None
        try:
            updater.activate(staged)
        except Exception as error:
            self.engine.state = IDLE
            self._update_status(str(error))
            rumps.alert(title="Не удалось установить обновление", message=str(error), ok="ОК")
            return
        if updater.restart():
            rumps.notification("Spoke", "Обновление установлено", "Перезапускаюсь…")
            rumps.quit_application()
            return
        self.engine.state = IDLE
        self._update_status("Обновление установлено. Перезапустите Spoke.")
        rumps.alert(title="Обновление установлено", message="Перезапустите Spoke.", ok="ОК")

    def _check_permissions(self) -> None:
        granted = permissions.all_granted()
        if granted and not self._permissions_ok:
            self.engine.restart_listening()
        self._permissions_ok = granted

    def _open_settings(self, _) -> None:
        if self.settings is None:
            self.settings = SettingsWindow(self.config, self.engine, self._settings_changed, self.check_updates, self._preview_overlay)
        self.settings.show()

    def _settings_changed(self, key: str) -> None:
        if key == "dictation_mode":
            self.engine.change_mode()
        if key in ("overlay", "overlay_bottom_margin", "overlay_size"):
            self.apply_overlay()

    def _preview_overlay(self, mode: str) -> None:
        if self.overlay is not None and self.engine.state == IDLE:
            self._preview_mode = mode
            self._preview_started = time.monotonic()
            self._sync_animation()

    def apply_overlay(self) -> None:
        if self.config.overlay and self.overlay is None:
            self.overlay = Overlay(self.config.overlay_bottom_margin, self.engine.cancel_locked, self.engine.finish_locked)
        elif not self.config.overlay and self.overlay is not None:
            self.overlay.close()
            self.overlay = None
        if self.overlay is not None:
            self.overlay.set_bottom_margin(self.config.overlay_bottom_margin)
            self.overlay.set_size(self.config.overlay_size)

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

    def _sync_animation(self):
        active = self.engine.state in (RECORDING, WORKING)
        if active and self._activity is None:
            self._activity = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
                NSActivityUserInitiatedAllowingIdleSystemSleep, "Spoke: обработка диктовки")
        elif not active and self._activity is not None:
            NSProcessInfo.processInfo().endActivity_(self._activity)
            self._activity = None
        if self.overlay is not None:
            self._overlay_timer.start()
            self._animate(None)

    def _animate(self, _) -> None:
        if self.overlay is None:
            self._overlay_timer.stop()
            return
        state = self.engine.state
        if state == RECORDING:
            self._preview_started = 0.0
            self.overlay.update(RECORD, getattr(self.engine.recorder, "level", 0.0), self.engine.locked)
        elif state == WORKING:
            if self.engine.formatting_field:
                self._preview_started = 0.0
                self.overlay.update(FIELD_FORMAT)
            else:
                self.overlay.update(WORK)
        else:
            now = time.monotonic()
            formatted_at = self.engine.field_formatted_at
            if formatted_at is not None and now - formatted_at < FIELD_CONFIRMATION_SECONDS:
                self.overlay.update(FIELD_FORMATTED)
                return
            elapsed = now - self._preview_started
            if elapsed < 4.6:
                if self._preview_mode == WORK:
                    self.overlay.update(WORK)
                else:
                    phrase = max(0.0, math.sin(elapsed * 3.0))
                    syllable = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(elapsed * 15)) ** 2
                    self.overlay.update(RECORD, 0.002 + 0.065 * phrase * syllable)
            else:
                self.overlay.update(None)
                if self.overlay.panel is None:
                    self._overlay_timer.stop()

    def _refresh(self, _) -> None:
        try:
            update_result = self._update_results.get_nowait()
        except Empty:
            pass
        else:
            self._show_update_result(*update_result)
        state = self.engine.state
        self._show_icon(state)
        self._install_when_idle()
        refresh_details = time.monotonic() - self._last_permissions >= 3
        if refresh_details:
            self._last_permissions = time.monotonic()
            self._check_permissions()
        if state == RECORDING and self.engine.locked:
            self.status_item.title = f"Постоянная запись: {self.config.hotkey_title()} — завершить, Esc — отмена"
        elif state == WORKING and self.engine.formatting_field:
            self.status_item.title = "Оформляю текст поля…"
        else:
            self.status_item.title = STATUS.get(state, "")
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
        if refresh_details and self.settings is not None and self.settings.visible:
            self.settings.refresh_permissions()
            self.settings.refresh_usage()

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
        if has:
            self.history_menu.add(None)
            self.history_menu.add(rumps.MenuItem("Очистить историю", callback=self._clear_history))
        else:
            self.history_menu.add(rumps.MenuItem("Пока пусто"))

    def _clear_history(self, _) -> None:
        self.engine.clear_history()

    def _paste_last(self, _) -> None:
        self.engine.paste_from_history(0)

    def _copy_last(self, _) -> None:
        if self.engine.history:
            inject.set_clipboard(self.engine.history[0])

    def _paste_history(self, index: int):
        def handler(_):
            self.engine.paste_from_history(index)
        return handler

def run() -> None:
    logging.basicConfig(filename=os.path.expanduser("~/Library/Logs/Spoke.log"), level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if (Path.home() / ".config/spoke/terminal/init.zsh").is_file():
        try:
            from spoke.terminal import install_integration
            install_integration()
        except OSError:
            logging.getLogger("spoke").exception("Не удалось обновить интеграцию cmux")
    config = cfg.load()
    config.apply_app_defaults()
    if not cfg.CONFIG_PATH.exists():
        cfg.save(config)
    if os.environ.get("RESOURCEPATH") and config.autostart != autostart.enabled():
        try:
            (autostart.enable if config.autostart else autostart.disable)()
        except OSError:
            logging.getLogger("spoke").exception("Не удалось настроить запуск при входе")
    SpokeApp(config).run()
