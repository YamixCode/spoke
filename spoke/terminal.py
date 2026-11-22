"""Format the current cmux line editor buffer without accessing scrollback."""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import tempfile
import socket
import subprocess
import time
from pathlib import Path

from spoke.terminal_editor import ROOT, receive, send, socket_path, surface_id

CMUX = "/Applications/cmux.app/Contents/Resources/bin/cmux"


def _cmux(*arguments):
    result = subprocess.run([CMUX, *arguments], capture_output=True, text=True, timeout=3)
    if result.returncode and ("Access denied" in result.stderr or "Доступ запрещён" in result.stderr):
        raise RuntimeError("cmux разрешает доступ только своим процессам. Запустите Spoke из cmux.")
    result.check_returncode()
    return result.stdout


def focused_surface():
    focused = json.loads(_cmux("--json", "--id-format", "both", "identify", "--no-caller"))["focused"]
    return (focused["workspace_id"], surface_id(focused["surface_id"])) if focused and focused.get("surface_type") == "terminal" else None


def _editor_kind(surface):
    try:
        registered, tty, phase = (ROOT / (surface + ".tty")).read_text().split()
        if not registered.isdecimal() or not re.fullmatch(r"ttys\d+", tty):
            raise ValueError()
        records = subprocess.run(["/bin/ps", "-t", tty, "-o", "pid=,ppid=,pgid=,tpgid=,comm="],
                                 capture_output=True, text=True, check=True, timeout=2).stdout
        rows = [line.split(None, 4) for line in records.splitlines()]
        parents = {row[0]: row[1] for row in rows}
        for pid, _, group, foreground, command in rows:
            if pid != foreground or group != foreground:
                continue
            ancestor = pid
            while ancestor in parents and ancestor != registered:
                ancestor = parents[ancestor]
            name = Path(command).name.lstrip("-")
            if ancestor == registered and (pid == registered and name == "zsh" and phase == "prompt" or name in ("codex", "claude")):
                return "zsh" if name == "zsh" else "editor"
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    raise RuntimeError("Для этой программы cmux не предоставляет редактируемый ввод. Для строки zsh подключите интеграцию Spoke.")


def capture_terminal(is_focused):
    from spoke.live_prompt import LivePrompt, target_process

    target = focused_surface()
    if target is None or not is_focused():
        raise RuntimeError("Поставьте курсор в строку ввода cmux.")
    process = target_process(target)
    if process[1] in ("claude", "codex"):
        field = LivePrompt(target, is_focused, process)
        field.validate()
        if field.rpc("surface.input_state").get("state") != "draft":
            raise RuntimeError("Поставьте курсор в непустой черновик терминала.")
        if (ROOT / (target[1] + ".tty")).is_file():
            variables = subprocess.run(
                ["/bin/ps", "eww", "-p", str(process[0]), "-o", "command="],
                capture_output=True, timeout=2, check=True).stdout.split()
            editor = os.fsencode(ROOT / "editor")
            if b"VISUAL=" + editor in variables and b"EDITOR=" + editor in variables:
                return TerminalField.capture(is_focused, target)
        return LivePrompt.capture(target, is_focused, process)
    return TerminalField.capture(is_focused, target)


class TerminalField:
    @classmethod
    def capture(cls, is_focused, target=None):
        target = target or focused_surface()
        if target is None:
            raise RuntimeError("Поставьте курсор в строку ввода cmux.")
        workspace, surface = target
        kind = _editor_kind(surface)
        field = cls()
        field._target, field._is_focused, field._kind = target, is_focused, kind
        field._stack = contextlib.ExitStack()
        try:
            path = Path(socket_path(surface))
            listener = field._stack.enter_context(socket.socket(socket.AF_UNIX))
            lock = field._stack.enter_context(path.with_suffix(".lock").open("a"))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("Эта вкладка уже оформляет черновик.") from error
            path.unlink(missing_ok=True)
            listener.bind(str(path))
            field._stack.callback(path.unlink, missing_ok=True)
            listener.listen(2)
            listener.settimeout(4)
            field._listener = listener
            field._validate()
            destination = ("--workspace", workspace, "--surface", surface)
            if kind == "zsh":
                _cmux("send-key", *destination, "ctrl+x")
            _cmux("send-key", *destination, "ctrl+g")
            connection, _ = listener.accept()
            field._connection = field._stack.enter_context(connection)
            connection.settimeout(5)
            field._stream = field._stack.enter_context(connection.makefile("rb"))
            draft = receive(field._stream)
            if draft.get("kind") != kind or not isinstance(draft.get("text"), str):
                raise RuntimeError("Терминал не передал редактируемый черновик.")
            field.text = draft["text"]
            if not field.text.strip():
                raise RuntimeError("В строке ввода нет текста для оформления.")
            field._validate()
            return field
        except BaseException as error:
            field.close()
            if isinstance(error, (OSError, ValueError, subprocess.SubprocessError)):
                raise RuntimeError("Терминал не открыл черновик. Проверьте фокус и привязку внешнего редактора.") from error
            raise

    def _validate(self):
        if not self._is_focused() or focused_surface() != self._target:
            raise RuntimeError("Вкладка терминала изменилась. Оформление остановлено.")

    def replace(self, text):
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("Получен пустой ответ. Черновик сохранён.")
        self._validate()
        send(self._connection, {"text": text})
        confirmed = receive(self._stream)
        if confirmed.get("confirmed") != text:
            raise RuntimeError("Редактор терминала не подтвердил замену.")
        if self._kind == "editor" and text != self.text:
            self._stream.read(1)
            time.sleep(0.15)
            self._validate()
            workspace, surface = self._target
            if _editor_kind(surface) != "editor":
                raise RuntimeError("Программа терминала изменилась во время оформления.")
            _cmux("send-key", "--workspace", workspace, "--surface", surface, "ctrl+g")
            connection, _ = self._listener.accept()
            with connection, connection.makefile("rb") as stream:
                connection.settimeout(3)
                draft = receive(stream)
                send(connection, {"keep": True})
                confirmed = {"confirmed": draft.get("text") if draft.get("kind") == "editor" else None}
        elif self._kind == "zsh":
            connection, _ = self._listener.accept()
            with connection, connection.makefile("rb") as stream:
                connection.settimeout(3)
                confirmed = receive(stream)
        if confirmed.get("confirmed") != text:
            raise RuntimeError("Редактор терминала не подтвердил замену.")
        self._validate()
        return text != self.text

    def close(self):
        self._stack.close()


def install_integration():
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    for source, name in (("terminal_editor.py", "editor"), ("terminal.zsh", "init.zsh")):
        asset = Path(__file__).with_name(source)
        if not asset.is_file() and os.environ.get("RESOURCEPATH"):
            asset = Path(os.environ["RESOURCEPATH"]) / "terminal" / source
        destination = ROOT / name
        data = asset.read_bytes()
        if destination.is_file() and destination.read_bytes() == data and os.access(destination, os.X_OK):
            continue
        with tempfile.NamedTemporaryFile(dir=ROOT, delete=False) as output:
            temp = Path(output.name)
            try:
                output.write(data)
                output.close()
                temp.chmod(0o700)
                temp.replace(destination)
            finally:
                temp.unlink(missing_ok=True)


def enable_integration():
    install_integration()
    startup = Path(os.environ.get("ZDOTDIR") or Path.home()) / ".zshrc"
    line = '[[ -r "$HOME/.config/spoke/terminal/init.zsh" ]] && source "$HOME/.config/spoke/terminal/init.zsh"'
    if not startup.exists() or line not in startup.read_text().splitlines():
        with startup.open("a") as output:
            output.write("\n" + line + "\n")


if __name__ == "__main__":
    enable_integration()
