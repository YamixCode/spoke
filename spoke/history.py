"""История диктовок на диске: переживает перезапуск и падение приложения."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


class HistoryStore:
    def __init__(self, path: Path, limit: int = 50):
        self.path, self.limit = path, max(1, limit)

    def load(self) -> list[str]:
        texts = []
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    texts.append(str(json.loads(line)["text"]))
                except (ValueError, KeyError, TypeError):
                    continue
        except OSError:
            return []
        return texts[-self.limit:][::-1]

    def add(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "text": text}, ensure_ascii=False) + "\n")
        lines = self.path.read_text(encoding="utf-8").splitlines()
        if len(lines) > self.limit * 4:
            self.path.write_text("\n".join(lines[-self.limit:]) + "\n", encoding="utf-8")

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
