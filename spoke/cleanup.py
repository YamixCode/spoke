"""Локальная обработка фрагментов диктовки моделью Spokenly Cleanup 1."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import ssl
import tempfile
import threading
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from urllib.request import Request, urlopen

import certifi

from spoke.config import DATA_DIR
from spoke.native_bridge import call_json

logger = logging.getLogger("spoke")
REVISION = "19212532215b2b64ca86c14b6b61129a4ab72a50"
REPOSITORY = "spokenly/cleanup-1-coreml"
MODEL = "CleanupNeuralEngine.mlmodelc"
FILES = {
    "LICENSE": (11358, "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"),
    "NOTICE": (550, "811a6a7343ca81683afde78fb76c891d07ca224bb24816703fcd6027e483753d"),
    "tokenizer.json": (11422650, "be75606093db2094d7cd20f3c2f385c212750648bd6ea4fb2bf507a6a4c55506"),
    f"{MODEL}/metadata.json": (30157, "0368252c088c0b9e7c717ed4bb6f69b2193a9c103092a3ef3fed29b259df9334"),
    f"{MODEL}/model.mil": (6204964, "453d88bf77513d28188eae1041de228f1c99e8e1812dd8f3c1e074d35e859a75"),
    f"{MODEL}/coremldata.bin": (1818, "3a04fbc91028655233134f21b07b9270397d1e11d4260fa345e6e7e05644995d"),
    f"{MODEL}/analytics/coremldata.bin": (243, "f8cbefb6bc0c5dd98be0def7bc051599ac59cd0432973c975cb45bfd91e1ec2a"),
    f"{MODEL}/weights/weight.bin": (753622016, "1f70a42c5773f4b873acf67ac887372a299cdff970abf7f15e82f62578b27b88"),
}


def _has_size(path: Path, size: int) -> bool:
    try:
        return path.stat().st_size == size
    except FileNotFoundError:
        return False


def _install_file(source, destination: Path, size: int, expected: str) -> None:
    digest = hashlib.sha256()
    written = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as staging:
        temp = Path(staging.name)
        try:
            while data := source.read(1024 * 1024):
                written += len(data)
                if written > size:
                    raise ValueError("Размер файла Cleanup превышает ожидаемый")
                staging.write(data)
                digest.update(data)
            if written != size or digest.hexdigest() != expected:
                raise ValueError("Контрольная сумма файла Cleanup не совпала")
            staging.close()
            temp.replace(destination)
        finally:
            temp.unlink(missing_ok=True)


def _install_model(directory: Path) -> None:
    snapshot = Path.home() / ".cache/huggingface/hub" / f"models--{REPOSITORY.replace('/', '--')}" / "snapshots" / REVISION
    for name, (size, digest) in FILES.items():
        path = directory / name
        if _has_size(path, size):
            continue
        cached = snapshot / name
        if _has_size(cached, size):
            try:
                with cached.open("rb") as source:
                    _install_file(source, path, size, digest)
                continue
            except (OSError, ValueError):
                logger.debug("Локальная копия файла Cleanup недоступна: %s", name)
        url = f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{name}"
        request = Request(url, headers={"User-Agent": "Spoke"})
        with urlopen(request, timeout=30, context=ssl.create_default_context(cafile=certifi.where())) as source:
            _install_file(source, path, size, digest)


def _tokenizer(path: Path):
    import tiktoken

    data = json.loads(path.read_bytes())
    # The ByteLevel alphabet stores each input byte as a single Unicode character.
    visible = [*range(33, 127), *range(161, 173), *range(174, 256)]
    missing = [byte for byte in range(256) if byte not in visible]
    alphabet = {chr(byte): byte for byte in visible}
    alphabet.update({chr(256 + index): byte for index, byte in enumerate(missing)})
    ranks = {bytes(alphabet[c] for c in token): rank for token, rank in data["model"]["vocab"].items()}
    return tiktoken.Encoding(
        "spokenly-cleanup-1",
        pat_str=data["pre_tokenizer"]["pretokenizers"][0]["pattern"]["Regex"],
        mergeable_ranks=ranks,
        special_tokens={},
    )


def _preserves_content(raw: str, cleaned: str) -> bool:
    if not cleaned:
        return False
    number_pattern = r"\d+(?:[.:,/-]\d+)*"
    numbers = re.findall(number_pattern, raw)
    if numbers and numbers != re.findall(number_pattern, cleaned):
        return False
    before = re.findall(r"\w+", raw.casefold())
    after = re.findall(r"\w+", cleaned.casefold())
    if [word for word in before if word in {"не", "ни", "not"}] != [word for word in after if word in {"не", "ни", "not"}]:
        return False
    # Discard major omissions or expansions. Allow normal punctuation, filler removal and small edits.
    if not before or not after or len(after) < len(before) * 0.7 or len(after) > len(before) * 1.3 + 1:
        return False
    if re.search(r"[а-яё]", raw.casefold()) and any(word.isascii() and word.isalpha() and word not in after for word in before):
        return False
    return SequenceMatcher(None, before, after, autojunk=False).ratio() >= 0.65


class Cleanup:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or DATA_DIR / "models" / f"cleanup-1-{REVISION[:8]}"
        self.status = "Подготовка…"
        self._encoding = None
        self._preparing = threading.Lock()
        self._retry_at = 0.0

    def prepare(self, *, wait: bool = False) -> None:
        if self._encoding is not None or time.monotonic() < self._retry_at:
            return
        if self._preparing.acquire(blocking=False):
            if wait:
                self._prepare()
            else:
                threading.Thread(target=self._prepare, daemon=True).start()
        elif wait:
            with self._preparing:
                pass

    def _prepare(self) -> None:
        try:
            if any(not _has_size(self.directory / name, size) for name, (size, _) in FILES.items()):
                self.status = "Загрузка модели…"
                _install_model(self.directory)
            self.status = "Подготовка…"
            encoding = _tokenizer(self.directory / "tokenizer.json")
            call_json("spoke_cleanup_prepare", os.fsencode(self.directory / MODEL))
            self._encoding = encoding
            self.status = "Готов"
        except Exception:
            self._retry_at = time.monotonic() + 60
            self.status = "Модель недоступна. Повтор при диктовке"
            logger.exception("Не удалось подготовить модель Cleanup")
        finally:
            self._preparing.release()

    def process(self, raw: str) -> str:
        if not raw.strip():
            return raw
        encoding = self._encoding
        if encoding is None:
            self.prepare()
            return raw
        try:
            text = unicodedata.normalize("NFC", " ".join(raw.split()))
            prompt = encoding.encode_ordinary(f"tone: formal\ntext: {text}\nout:\n")
            guesses = encoding.encode_ordinary(text)
            if len(prompt) + len(guesses) >= 512:
                return raw
            output = call_json("spoke_cleanup_generate", json.dumps({"prompt": prompt, "guesses": guesses}).encode())
            cleaned = encoding.decode(output["tokens"]).strip()
            return cleaned if _preserves_content(raw, cleaned) else raw
        except Exception:
            logger.exception("Не удалось отредактировать фрагмент диктовки")
            return raw
