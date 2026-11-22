"""Read and replace the focused editable field through macOS Accessibility."""
from __future__ import annotations

import logging
import os
import tempfile
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from spoke.config import DATA_DIR
from spoke.inject import copy_from_application, paste_into_application, press_command_key


def _read(element, name):
    import ApplicationServices as ax

    status, value = ax.AXUIElementCopyAttributeValue(element, name, None)
    return value if status == ax.kAXErrorSuccess else None


def _writable(element, name) -> bool:
    import ApplicationServices as ax

    status, writable = ax.AXUIElementIsAttributeSettable(element, name, None)
    return status == ax.kAXErrorSuccess and writable


def _frontmost_application():
    from AppKit import NSWorkspace

    return NSWorkspace.sharedWorkspace().frontmostApplication()


def _frontmost_pid() -> int | None:
    application = _frontmost_application()
    return application.processIdentifier() if application is not None else None


def _editor_text(text: str) -> str:
    return unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def _paste_chunks(text: str, limit: int = 2500):
    # Keep long pastes below limits that can turn them into attachments.
    start = 0
    while len(text) - start > limit:
        end = start + limit
        boundary = text.rfind("\n", start + limit // 2, end)
        if boundary == -1:
            boundary = text.rfind(" ", start + limit // 2, end)
        end = boundary + 1 if boundary != -1 else end
        yield text[start:end]
        start = end
    if start < len(text):
        yield text[start:]


def _web_editor(element) -> bool:
    import ApplicationServices as ax

    for _ in range(16):
        element = _read(element, ax.kAXParentAttribute)
        if element is None:
            break
        if _read(element, ax.kAXRoleAttribute) == "AXWebArea":
            return True
    return False


@dataclass(frozen=True)
class EditableField:
    _app: object = field(repr=False)
    _pid: int
    _element: object = field(repr=False)
    _role: str
    text: str = field(repr=False)
    _web: bool = False

    @classmethod
    def capture(cls) -> EditableField:
        import ApplicationServices as ax

        if not ax.AXIsProcessTrusted():
            raise RuntimeError("Разрешите Spoke «Универсальный доступ», чтобы оформить текст поля.")
        application = _frontmost_application()
        pid = application.processIdentifier() if application is not None else None
        if pid is None or pid == os.getpid():
            raise RuntimeError("Поставьте курсор в текстовое поле другого приложения.")
        app = ax.AXUIElementCreateApplication(pid)
        ax.AXUIElementSetMessagingTimeout(app, 0.35)
        # Reading the app role activates basic accessibility in Chromium.
        _read(app, ax.kAXRoleAttribute)
        element = _read(app, ax.kAXFocusedUIElementAttribute)
        for _ in range(4):
            if element is not None or _frontmost_pid() != pid:
                break
            time.sleep(0.1)
            element = _read(app, ax.kAXFocusedUIElementAttribute)
        bundle = application.bundleIdentifier()
        if element is not None and (_read(element, ax.kAXSubroleAttribute) == ax.kAXSecureTextFieldSubrole
                                    or _read(element, "AXProtectedContent") is True):
            raise RuntimeError("Текущее поле не поддерживает оформление текста.")
        if bundle == "com.cmuxterm.app" and (
                element is None or (_read(element, ax.kAXRoleAttribute) == ax.kAXTextAreaRole
                                    and not _web_editor(element))):
            from spoke.terminal import capture_terminal
            return capture_terminal(lambda: _frontmost_pid() == pid)
        if element is None:
            raise RuntimeError("Поставьте курсор в редактируемое текстовое поле.")
        if _frontmost_pid() != pid:
            raise RuntimeError("Приложение в фокусе изменилось. Повторите команду в нужном поле.")
        ax.AXUIElementSetMessagingTimeout(element, 0.35)
        role = _read(element, ax.kAXRoleAttribute)
        subrole = _read(element, ax.kAXSubroleAttribute)
        if (role not in (ax.kAXTextAreaRole, ax.kAXTextFieldRole)
                or subrole == ax.kAXSecureTextFieldSubrole
                or _read(element, "AXProtectedContent") is True):
            raise RuntimeError("Текущее поле не поддерживает оформление текста.")
        range_writable = _writable(element, ax.kAXSelectedTextRangeAttribute)
        if bundle in ("com.apple.Terminal", "com.googlecode.iterm2", "com.mitchellh.ghostty", "dev.warp.Warp-Stable") and role == ax.kAXTextAreaRole:
            raise RuntimeError("Для оформления строки терминала используйте интеграцию cmux.")
        value_writable = _writable(element, ax.kAXValueAttribute)
        selection_writable = range_writable and _writable(element, ax.kAXSelectedTextAttribute)
        if not (value_writable or selection_writable):
            raise RuntimeError("Приложение не разрешает Spoke заменить текст в этом поле.")
        text = _read(element, ax.kAXValueAttribute)
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("В текущем поле нет текста для оформления.")
        target = cls(app, pid, element, role, text, _web_editor(element))
        target._check_unchanged()
        return target

    def _is_focused(self) -> bool:
        import ApplicationServices as ax
        from CoreFoundation import CFEqual

        current = _read(self._app, ax.kAXFocusedUIElementAttribute) if _frontmost_pid() == self._pid else None
        return current is not None and CFEqual(current, self._element)

    def _check_unchanged(self, expected: str | None = None) -> None:
        if not self._is_focused() or self._value() != (self.text if expected is None else expected):
            raise RuntimeError("Поле или его содержимое изменилось. Текст не заменён.")

    def _value(self):
        import ApplicationServices as ax

        return _read(self._element, ax.kAXValueAttribute)

    def replace(self, text: str) -> bool:
        import ApplicationServices as ax

        self._check_unchanged()
        if not text.strip():
            raise RuntimeError("Получен пустой ответ. Исходный текст сохранён.")
        if _editor_text(text) == _editor_text(self.text):
            return False
        if self._role == ax.kAXTextFieldRole and ("\n" in text or "\r" in text):
            raise RuntimeError("Для оформления с абзацами нужно многострочное поле.")

        directory = DATA_DIR / "recovery"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix="spoke-draft-", suffix=".txt", delete=False) as backup_file:
            backup_file.write(self.text)
        backup = Path(backup_file.name)
        try:
            if self._web:
                self._paste_in_web_editor(text)
            else:
                self._replace_native(text)
        except Exception as error:
            if not self._web and self._value() == self.text:
                backup.unlink(missing_ok=True)
                raise
            if self._undo_empty_replacement():
                backup.unlink(missing_ok=True)
                raise RuntimeError("Вставка очистила поле. Spoke вернул исходный текст.") from error
            raise RuntimeError(f"{error} Копия исходного черновика: {backup}.") from error
        backup.unlink(missing_ok=True)
        return True

    def _replace_native(self, text: str) -> None:
        import ApplicationServices as ax
        from CoreFoundation import CFRangeMake

        element = self._element
        if (_writable(element, ax.kAXSelectedTextAttribute)
                and _writable(element, ax.kAXSelectedTextRangeAttribute)):
            previous_range = _read(element, ax.kAXSelectedTextRangeAttribute)
            length = len(self.text.encode("utf-16-le")) // 2
            whole_text = ax.AXValueCreate(ax.kAXValueCFRangeType, CFRangeMake(0, length))
            if ax.AXUIElementSetAttributeValue(element, ax.kAXSelectedTextRangeAttribute, whole_text) == ax.kAXErrorSuccess:
                try:
                    self._check_unchanged()
                    if (_read(element, ax.kAXSelectedTextAttribute) == self.text
                            and self._apply(ax.kAXSelectedTextAttribute, text)):
                        return
                finally:
                    if previous_range is not None and _read(element, ax.kAXValueAttribute) == self.text:
                        ax.AXUIElementSetAttributeValue(element, ax.kAXSelectedTextRangeAttribute, previous_range)

        if _writable(element, ax.kAXValueAttribute):
            self._check_unchanged()
            if self._apply(ax.kAXValueAttribute, text):
                return
        raise RuntimeError("Приложение не смогло заменить текст.")

    def _apply(self, attribute: str, text: str) -> bool:
        import ApplicationServices as ax

        if ax.AXUIElementSetAttributeValue(self._element, attribute, text) != ax.kAXErrorSuccess:
            return False
        actual = self._wait_for_change(self.text)
        return actual is not None and _editor_text(actual) == _editor_text(text)

    def _paste_in_web_editor(self, text: str) -> None:
        import ApplicationServices as ax

        previous_range = _read(self._element, ax.kAXSelectedTextRangeAttribute)
        try:
            started = time.monotonic()
            self._select_web_text(self.text)
            before = self.text
            caret = None
            chunks = list(_paste_chunks(text))
            for index, chunk in enumerate(chunks):
                def validate() -> None:
                    self._check_unchanged(before)
                    if index == 0 and not self._web_text_selected(before):
                        raise RuntimeError("Редактор не выделил весь текст. Поле не заменено.")
                    if index > 0 and _read(self._element, ax.kAXSelectedTextRangeAttribute) != caret:
                        raise RuntimeError("Положение курсора изменилось. Вставка остановлена.")

                inserted = paste_into_application(chunk, self._pid, validate, lambda: self._wait_for_change(before))
                if inserted is None and index == len(chunks) - 1:
                    break
                if inserted is None:
                    raise RuntimeError("Веб-редактор не подтвердил вставку.")
                before = inserted
                caret = _read(self._element, ax.kAXSelectedTextRangeAttribute)
            # A web editor can briefly expose pasted text before rerendering its field.
            pasted = time.monotonic()
            final = self._wait_for_change(self.text, stable_for=0.40)
            if final is None:
                final = self._value()
            if not isinstance(final, str) or not final.strip():
                raise RuntimeError("Веб-редактор не сохранил текст после вставки.")
            self._verify_web_text(text, final)
            logging.getLogger("spoke").info("Веб-поле: вставка %.2f с, проверка %.2f с",
                                            pasted - started, time.monotonic() - pasted)
        finally:
            if (previous_range is not None and _frontmost_pid() == self._pid
                    and _read(self._element, ax.kAXValueAttribute) == self.text):
                ax.AXUIElementSetAttributeValue(self._element, ax.kAXSelectedTextRangeAttribute, previous_range)

    def _web_text_selected(self, expected: str) -> bool:
        import ApplicationServices as ax

        # Chromium can omit paragraph separators from the accessibility selection.
        selection = _read(self._element, ax.kAXSelectedTextAttribute)
        return isinstance(selection, str) and "".join(_editor_text(selection).split()) == "".join(
            _editor_text(expected).split()
        )

    def _select_web_text(self, expected: str) -> None:
        self._check_unchanged(expected)
        press_command_key(0, self._pid)  # A
        deadline = time.monotonic() + 0.45
        while not self._web_text_selected(expected):
            if time.monotonic() >= deadline:
                raise RuntimeError("Веб-редактор не выделил весь текст поля.")
            self._check_unchanged(expected)
            time.sleep(0.025)

    def _verify_web_text(self, expected: str, observed: str | None = None) -> None:
        import ApplicationServices as ax

        observed = expected if observed is None else observed
        original = _editor_text(self.text).strip()

        def validate() -> None:
            self._check_unchanged(observed)

        validate()
        caret = _read(self._element, ax.kAXSelectedTextRangeAttribute)
        try:
            self._select_web_text(observed)
            copied = copy_from_application(self._pid, validate)
            copied_text = _editor_text(copied).strip() if copied else ""
            if not copied_text or copied_text == original or copied_text != _editor_text(expected).strip():
                raise RuntimeError("Веб-редактор не подтвердил текст после вставки.")
        finally:
            if caret is not None and self._is_focused() and self._value() == observed:
                ax.AXUIElementSetAttributeValue(self._element, ax.kAXSelectedTextRangeAttribute, caret)

    def _undo_empty_replacement(self) -> bool:
        value = self._value()
        if not isinstance(value, str) or value.strip() or not self._is_focused():
            return False
        press_command_key(6, self._pid)
        restored = self._wait_for_change(value)
        return restored is not None and _editor_text(restored) == _editor_text(self.text)

    def _wait_for_change(self, previous: str, *, stable_for: float = 0.075) -> str | None:
        # Browser renderers can acknowledge the AX request before updating the field.
        deadline = time.monotonic() + 0.60
        last_value, stable_since = None, time.monotonic()
        while True:
            current = self._value()
            now = time.monotonic()
            if current != last_value:
                last_value, stable_since = current, now
            if isinstance(current, str) and current.strip() and current != previous and now - stable_since >= stable_for:
                return current
            if now >= deadline:
                return None
            time.sleep(0.025)
