"""Read the active terminal composer by following its own editing cursor."""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
import unicodedata
from pathlib import Path

from spoke.config import DATA_DIR
from spoke.terminal import CMUX, _cmux, focused_surface

UNAVAILABLE = "Не удалось определить весь редактируемый ввод терминала."
CHANGED = "Фокус или черновик терминала изменился. Оформление остановлено."
PLACEHOLDER = re.compile(r"\[(?:Pasted (?:Content|text)|Image|Attachment)(?:\s|#|:|\])", re.I)


def target_process(target):
    workspace, surface = target
    tree = json.loads(_cmux("--json", "--id-format", "both", "top", "--workspace", workspace))
    for window in tree["windows"]:
        for ws in window["workspaces"]:
            for pane in ws["panes"]:
                for item in pane["surfaces"]:
                    if item["id"].lower() != surface:
                        continue
                    tty = item.get("tty") or ""
                    if re.fullmatch(r"ttys\d+", tty):
                        rows = subprocess.check_output(
                            ["/bin/ps", "-t", tty, "-o", "pid=,tpgid=,comm="], text=True, timeout=2)
                        for row in rows.splitlines():
                            pid, fg, path = row.split(None, 2)
                            if pid == fg:
                                return (int(pid), Path(path).name.lstrip("-"))
    raise RuntimeError(UNAVAILABLE)


class Grid:
    def __init__(self, data, program):
        cursor = data.get("cursor", {})
        if not cursor.get("visible") or data.get("anchor") != "screen":
            raise RuntimeError(UNAVAILABLE)
        self.position = cursor["row"], cursor["column"]
        self.width = data["columns"]
        self.lines = [[" "] * self.width for _ in range(data["rows"])]
        for span in data["row_spans"]:
            row = self.lines[span["row"]]
            cells = []
            for char in span["text"]:
                if unicodedata.combining(char) and cells:
                    cells[-1] += char
                else:
                    cells.append(char)
                    if unicodedata.east_asian_width(char) in ("W", "F"):
                        cells.append("")
            left, size = span["column"], span["cell_width"]
            row[left:left + size] = cells if len(cells) == size else [None] * size
        row, col = self.position
        if not 0 <= row < len(self.lines) or not 0 <= col <= self.width:
            raise RuntimeError(UNAVAILABLE)
        marker = "›" if program == "codex" else "❯"
        self.prompt = next((i for i in range(row, -1, -1)
                            if self.lines[i][0] == marker and self.lines[i][1] in (" ", "\xa0")), None)
        if self.prompt is None or col < 2:
            raise RuntimeError(UNAVAILABLE)
        if any(self.lines[i][:2] != [" ", " "] for i in range(self.prompt + 1, row + 1)):
            raise RuntimeError(UNAVAILABLE)
        if program == "claude" and (self.prompt == 0 or self.lines[self.prompt - 1][:3] != ["─"] * 3):
            raise RuntimeError(UNAVAILABLE)
        self.paste_enabled = any(m["code"] == 2004 and m["on"] for m in data["modes"])

    def segment(self, row, left, right):
        cells = self.lines[row][left:right]
        if None in cells:
            raise RuntimeError(UNAVAILABLE)
        return "".join(cells)


class LivePrompt:
    def __init__(self, target, focused, process):
        self.target, self.focused, self.process = target, focused, process
        self.params = dict(zip(("workspace_id", "surface_id"), target))

    def validate(self):
        if not self.focused() or focused_surface() != self.target:
            raise RuntimeError(CHANGED)
        pid, program = self.process
        info = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "tpgid=,comm="], capture_output=True,
                              text=True, timeout=2).stdout.strip().split(None, 1)
        if len(info) != 2 or info[0] != str(pid) or Path(info[1]).name.lstrip("-") != program:
            raise RuntimeError(CHANGED)

    def rpc(self, method, **args):
        reply = subprocess.run([CMUX, "rpc", method, "-"], input=json.dumps({**self.params, **args}),
                               capture_output=True, text=True, check=True, timeout=4)
        return json.loads(reply.stdout)

    def grid(self):
        return Grid(self.rpc("terminal.replay", anchor="screen")["render_grid"], self.process[1])

    def move(self, key):
        self.validate()
        _cmux("send-key", "--workspace", self.target[0], "--surface", self.target[1], key)
        time.sleep(0.055)
        return self.grid()

    def beginning(self):
        g = self.move("Home")
        for _ in range(len(g.lines) + 1):
            if g.position == (g.prompt, 2):
                left = self.move("Left")
                if left.position == g.position and left.lines[g.prompt:] == g.lines[g.prompt:]:
                    return left
                raise RuntimeError(UNAVAILABLE)
            before = g.position
            self.move("Left")
            g = self.move("Home")
            if g.position >= before:
                break
        raise RuntimeError(UNAVAILABLE)

    def advance(self, count):
        self.validate()
        for offset in range(0, count, 32):
            self.rpc("terminal.input", text="\x1b[C" * min(32, count - offset))
            time.sleep(0.09)
        return self.grid()

    def wrapped_line(self, start, end):
        home = "ctrl+a" if self.process[1] == "claude" else "Home"
        g = self.move(home)
        if g.position != start.position or g.prompt != end.prompt:
            raise RuntimeError(UNAVAILABLE)
        pieces = []
        while g.position[0] < end.position[0]:
            row, col = g.position
            if col >= g.width:
                raise RuntimeError(UNAVAILABLE)
            remainder = g.segment(row, col, g.width)
            count = min(32, max(1, len(remainder.rstrip(" ")) - 1))
            piece = remainder[:count]
            after = self.advance(count)
            if after.prompt != start.prompt or after.position < g.position:
                raise RuntimeError(UNAVAILABLE)
            if after.position == g.position:
                raise RuntimeError(UNAVAILABLE)
            if after.position[0] == row:
                if after.segment(row, col, after.position[1]) != piece:
                    raise RuntimeError(UNAVAILABLE)
            elif count == 1 and after.position == (row + 1, 2):
                if not piece.isascii() and unicodedata.combining(piece):
                    raise RuntimeError(UNAVAILABLE)
                first = after.segment(row + 1, 2, 3)
                if not first or first.isspace():
                    raise RuntimeError(UNAVAILABLE)
                for _ in range(g.width):
                    following = self.move("Right")
                    if following.position != after.position:
                        if following.position[0] != row + 1 or following.segment(row + 1, 2, following.position[1]) != first:
                            raise RuntimeError(UNAVAILABLE)
                        after = following
                        piece += first
                        break
                    piece += " "
                else:
                    raise RuntimeError(UNAVAILABLE)
            else:
                raise RuntimeError(UNAVAILABLE)
            pieces.append(piece)
            g = after
        if g.position[0] != end.position[0] or g.position[1] > end.position[1]:
            raise RuntimeError(UNAVAILABLE)
        pieces.append(g.segment(g.position[0], g.position[1], end.position[1]))
        actual = self.move("ctrl+e" if self.process[1] == "claude" else "End")
        if actual.position != end.position:
            raise RuntimeError(UNAVAILABLE)
        return "".join(pieces)

    def read(self):
        g = self.beginning()
        origin = g.prompt
        parts = []
        for _ in range(len(g.lines)):
            row, start = g.position
            end = self.move("ctrl+e" if self.process[1] == "claude" else "End")
            last, col = end.position
            if end.prompt != origin or last < row or (last == row and col < start):
                raise RuntimeError(UNAVAILABLE)
            if last != row:
                parts.append(self.wrapped_line(g, end))
            else:
                parts.append(end.segment(row, start, col))
            g = self.move("Right")
            if g.prompt != origin:
                raise RuntimeError(UNAVAILABLE)
            if g.position == end.position:
                break
            if g.position != (last + 1, 2):
                raise RuntimeError(UNAVAILABLE)
        else:
            raise RuntimeError(UNAVAILABLE)
        value = "\n".join(parts)
        if not value.strip() or PLACEHOLDER.search(value):
            raise RuntimeError("Ввод пуст или содержит свёрнутую вставку либо вложение.")
        return value

    @classmethod
    def capture(cls, target, focused, process):
        field = cls(target, focused, process)
        field.validate()
        if field.rpc("surface.input_state").get("state") != "draft":
            raise RuntimeError(UNAVAILABLE)
        field.grid()
        field.text = field.read()
        return field

    def replace(self, text):
        if not text.strip() or any(ord(c) < 32 and c != "\n" for c in text) or "\x7f" in text:
            raise RuntimeError("Ответ редактора нельзя вставить в терминал.")
        if self.read() != self.text:
            raise RuntimeError(CHANGED)
        if text == self.text:
            return False
        g = self.beginning()
        if not g.paste_enabled:
            raise RuntimeError("Терминал не поддерживает вставку многострочного текста.")
        directory = DATA_DIR / "recovery"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, prefix="spoke-draft-",
                                         suffix=".txt", delete=False) as file:
            file.write(self.text)
        backup = Path(file.name)
        try:
            self.validate()
            receipt = self.rpc("terminal.paste", text=text, submit_key="none")
            if receipt.get("submitted") is not False:
                raise RuntimeError("Терминал не подтвердил режим вставки черновика.")
            time.sleep(0.12)
            self._boundary = self.grid().position
            if self.read() != text + self.text:
                raise RuntimeError("Терминал не подтвердил вставку перед исходным текстом.")
            self.beginning()
            self._advance(text)
            self.validate()
            for offset in range(0, len(self.text), 32):
                self.validate()
                self.rpc("terminal.input", text="\x1b[3~" * min(32, len(self.text) - offset))
                time.sleep(0.09)
            if self.read() != text:
                raise RuntimeError("Терминал не подтвердил полную замену.")
        except Exception as error:
            raise RuntimeError(f"{error} Исходный черновик сохранён в {backup}.") from error
        backup.unlink(missing_ok=True)
        return True

    def _advance(self, text):
        before = self.grid()
        after = self.advance(len(text))
        if (after.prompt != before.prompt or after.position == before.position
                or after.position != self._boundary):
            raise RuntimeError("Терминал не подтвердил границу замены.")
