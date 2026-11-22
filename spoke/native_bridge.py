from __future__ import annotations

import ctypes
import json
import os
from functools import cache
from pathlib import Path

LIBRARY = "libSpokeUI.dylib"


def library_path() -> Path:
    candidates = [Path(__file__).resolve().parent / "native" / LIBRARY]
    if os.environ.get("RESOURCEPATH"):
        candidates.append(Path(os.environ["RESOURCEPATH"]) / "native" / LIBRARY)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError("Не найдена нативная библиотека Spoke: соберите её скриптом scripts/build_native.sh")


@cache
def load_library() -> ctypes.CDLL:
    return ctypes.CDLL(str(library_path()))

_CancelCheck = ctypes.CFUNCTYPE(ctypes.c_int)


def call_json(symbol: str, *arguments: bytes):
    return _invoke(symbol, [ctypes.c_char_p] * len(arguments), arguments)


def call_json_cancellable(symbol, cancelled, *arguments: bytes):
    check = _CancelCheck(lambda: int(cancelled.is_set()))
    return _invoke(symbol, [ctypes.c_char_p] * len(arguments) + [_CancelCheck], (*arguments, check))


def _invoke(symbol, types, arguments):
    library = load_library()
    native = getattr(library, symbol)
    native.argtypes = types
    native.restype = ctypes.c_void_p
    pointer = native(*arguments)
    if not pointer:
        raise RuntimeError("Spoke не получил ответ от нативной библиотеки")
    try:
        answer = json.loads(ctypes.string_at(pointer))
    finally:
        release = library.spoke_apple_free
        release.argtypes = [ctypes.c_void_p]
        release.restype = None
        release(pointer)
    if "error" in answer:
        raise RuntimeError(answer["error"])
    return answer
