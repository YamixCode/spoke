"""Запуск: `python -m spoke` (иконка в строке меню). Проверки без интерфейса:
    python -m spoke --selftest файл.wav|ogg    распознать файл и показать время (холодный и тёплый проход)
    python -m spoke --devices                 список микрофонов"""
from __future__ import annotations

import sys
import time


def selftest(path: str) -> None:
    from mlx_whisper.audio import load_audio
    from spoke import config as cfg
    from spoke.engine import make_transcriber
    config = cfg.load()
    import numpy as np
    audio = np.array(load_audio(path), dtype=np.float32)
    transcriber = make_transcriber(config.recognition_backend, config.model)
    for label in ("холодный (с загрузкой модели)", "тёплый"):
        started = time.monotonic()
        text = transcriber.transcribe(audio, config.whisper_language(), config.prompt())
        print(f"{label}: {time.monotonic() - started:.1f} с на {len(audio) / 16000:.0f} с звука\n   {text}")


def main() -> None:
    if "--devices" in sys.argv:
        import sounddevice as sd
        print(sd.query_devices())
    elif "--selftest" in sys.argv:
        selftest(sys.argv[sys.argv.index("--selftest") + 1])
    else:
        from spoke.app import run
        run()


if __name__ == "__main__":
    main()
