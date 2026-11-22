"""Запуск: `python -m spoke` (иконка в строке меню). Проверки без интерфейса:
    python -m spoke --selftest файл.wav|ogg    распознать файл и показать время (первый и повторный проход)
    python -m spoke --devices                 список микрофонов"""
from __future__ import annotations

import sys
import time


def selftest(path: str) -> None:
    import subprocess

    import numpy as np

    from spoke.apple_speech import AppleTranscriber
    from spoke.cleanup import Cleanup
    from spoke.config import load
    from spoke.transcribe import SAMPLE_RATE

    config = load()
    pcm = subprocess.check_output(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"])
    audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
    cleanup = Cleanup()
    transcriber = AppleTranscriber(cleanup)
    transcriber.warm_up()
    cleanup.prepare(wait=True)
    for label in ("первый проход", "повторный проход"):
        started = time.monotonic()
        text = transcriber.transcribe(audio, config.vocabulary)
        print(f"{label}: {time.monotonic() - started:.1f} с на {len(audio) / SAMPLE_RATE:.0f} с звука\n   {text}")


def main() -> None:
    if "--install-terminal" in sys.argv:
        from spoke.terminal import enable_integration
        enable_integration()
    elif "--devices" in sys.argv:
        import sounddevice as sd
        print(sd.query_devices())
    elif "--selftest" in sys.argv:
        selftest(sys.argv[sys.argv.index("--selftest") + 1])
    else:
        from spoke.app import run
        run()


if __name__ == "__main__":
    main()
