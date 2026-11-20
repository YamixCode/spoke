"""Общие параметры звука, передаваемого распознавателю."""
from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16000


def is_silence(audio: np.ndarray, rms_threshold: float) -> bool:
    return audio.size == 0 or float(np.sqrt(np.mean(np.square(audio)))) < rms_threshold
