"""История диктовок на диске: переживает перезапуск и падение приложения."""
from __future__ import annotations

import json
import tempfile
import threading
import time
from pathlib import Path


class HistoryStore:
    def __init__(self, path: Path, limit: int = 50):
        self.path, self.limit = path, max(1, limit)
        self._lock = threading.Lock()

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
        line = json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "text": text}, ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                lines = self.path.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                lines = []
            lines.append(line)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
                pending = Path(handle.name)
                try:
                    handle.write("\n".join(lines[-self.limit:]) + "\n")
                    handle.close()
                    pending.replace(self.path)
                finally:
                    pending.unlink(missing_ok=True)

    def clear(self) -> None:
        with self._lock:
            self.path.unlink(missing_ok=True)
