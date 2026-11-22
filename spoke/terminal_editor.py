#!/usr/bin/python3
"""Exchange a single terminal draft with the focused Spoke session."""
from __future__ import annotations

import json
import os
import shlex
import socket
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path.home() / ".config/spoke/terminal"
LIMIT = 20_000


def surface_id(value):
    return str(UUID(value)).lower()


def socket_path(surface):
    directory = Path("/tmp") / f"spoke-drafts-{os.getuid()}"
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not directory.is_dir() or directory.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("Каталог связи с терминалом недоступен.")
    return str(directory / surface_id(surface))


def send(connection, value):
    connection.sendall(json.dumps(value, ensure_ascii=False).encode() + b"\n")


def receive(stream):
    line = stream.readline(256_001)
    if not line.endswith(b"\n"):
        raise ValueError("Терминал закрыл соединение.")
    return json.loads(line)


def editor(arguments):
    command = shlex.split(os.environ.get("SPOKE_PREVIOUS_EDITOR") or "vi")
    os.execvp(command[0], command + arguments)


def main(arguments):
    shell = arguments[0:1] == ["--zle"]
    ack = arguments == ["--confirm"]
    if not shell and not ack and (len(arguments) != 1 or not os.environ.get("CMUX_SURFACE_ID")):
        editor(arguments)
    with socket.socket(socket.AF_UNIX) as connection:
        try:
            connection.connect(socket_path(os.environ["CMUX_SURFACE_ID"]))
        except (ConnectionError, FileNotFoundError):
            if not shell and not ack:
                editor(arguments)
            return 1
        connection.settimeout(120)
        with connection.makefile("rb") as stream:
            if ack:
                send(connection, {"confirmed": sys.stdin.read(LIMIT + 1)})
                return 0
            path = Path(arguments[-1])
            if not path.is_file() or path.stat().st_size > 4 * LIMIT:
                send(connection, {"error": "Черновик недоступен или слишком велик."})
                return 1
            original = path.read_text(encoding="utf-8")
            send(connection, {"kind": "zsh" if shell else "editor", "text": original})
            response = receive(stream)
            if not response or path.read_text(encoding="utf-8") != original:
                return 1
            if response.get("keep"):
                return 0
            text = response["text"]
            if text != original:
                path.write_text(text, encoding="utf-8")
            send(connection, {"confirmed": path.read_text(encoding="utf-8")})
            return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, ValueError, RuntimeError):
        sys.exit(1)
