"""Локальный учёт облачных запросов по текущим ключам."""
from __future__ import annotations

import hashlib
import logging
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from spoke.config import DATA_DIR
from spoke.transcribe import SAMPLE_RATE

ELEVENLABS_HOURLY_USD = 0.22
HAIKU_INPUT_USD_PER_MILLION = 0.10
HAIKU_OUTPUT_USD_PER_MILLION = 0.50
logger = logging.getLogger("spoke")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    month TEXT NOT NULL, provider TEXT NOT NULL, fingerprint TEXT NOT NULL,
    samples INTEGER NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
    usd_nanos INTEGER NOT NULL,
    PRIMARY KEY (month, provider, fingerprint)
)
"""


def key_fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest() if key else ""


class UsageStore:
    def __init__(self, path: Path = DATA_DIR / "api-usage.sqlite3"):
        self.path = path

    def audio(self, key: str, samples: int) -> None:
        if samples:
            cost = samples * ELEVENLABS_HOURLY_USD / (SAMPLE_RATE * 3600)
            self._record("elevenlabs", key, samples, 0, 0, cost)

    def tokens(self, key: str, input_tokens: int, output_tokens: int) -> None:
        if input_tokens or output_tokens:
            cost = (input_tokens * HAIKU_INPUT_USD_PER_MILLION + output_tokens * HAIKU_OUTPUT_USD_PER_MILLION) / 1_000_000
            self._record("anthropic", key, 0, input_tokens, output_tokens, cost)

    def _record(self, provider: str, key: str, samples: int, input_tokens: int, output_tokens: int, cost: float) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with closing(sqlite3.connect(self.path, timeout=1)) as db, db:
                db.execute(_SCHEMA)
                db.execute("""
                    INSERT INTO usage VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(month, provider, fingerprint) DO UPDATE SET
                        samples = usage.samples + excluded.samples,
                        input_tokens = usage.input_tokens + excluded.input_tokens,
                        output_tokens = usage.output_tokens + excluded.output_tokens,
                        usd_nanos = usage.usd_nanos + excluded.usd_nanos
                    """, (date.today().strftime("%Y-%m"), provider, key_fingerprint(key), samples,
                          input_tokens, output_tokens, round(cost * 1_000_000_000)))
        except (OSError, sqlite3.Error):
            logger.warning("Не удалось сохранить локальный расход API")

    def totals(self, fingerprints: dict[str, str], *, this_month: bool) -> dict:
        result = {}
        for provider, fingerprint in fingerprints.items():
            result[provider] = {"seconds": 0.0, "input": 0, "output": 0, "usd": 0.0, "configured": bool(fingerprint)}
        if not self.path.is_file():
            return result
        query = """SELECT SUM(samples), SUM(input_tokens), SUM(output_tokens), SUM(usd_nanos)
                   FROM usage WHERE provider = ? AND fingerprint = ?"""
        params = ()
        if this_month:
            query += " AND month = ?"
            params = (date.today().strftime("%Y-%m"),)
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            for provider, fingerprint in fingerprints.items():
                if fingerprint:
                    samples, incoming, outgoing, nanos = db.execute(query, (provider, fingerprint, *params)).fetchone()
                    result[provider].update(seconds=(samples or 0) / SAMPLE_RATE, input=incoming or 0,
                                            output=outgoing or 0, usd=(nanos or 0) / 1_000_000_000)
        return result


usage_store = UsageStore()
