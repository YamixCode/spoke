from __future__ import annotations

import ctypes
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
