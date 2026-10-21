"""Декодирование записи из браузера (ogg/webm opus) в 16 кГц моно через ffmpeg."""
from __future__ import annotations

import subprocess

import numpy as np

SAMPLE_RATE = 16000
MAX_SECONDS = 180
_FFMPEG_TIMEOUT = 30


class AudioError(Exception):
    """Запись не удалось прочитать: пустая, битая или слишком длинная."""


def decode(data: bytes) -> np.ndarray:
    if not data:
        raise AudioError("пустая запись")
    # protocol_whitelist: присланные байты не смогут увести ffmpeg читать файлы или сеть (плейлисты и т.п.)
    cmd = ["ffmpeg", "-loglevel", "error", "-protocol_whitelist", "pipe", "-i", "pipe:0", "-t", str(MAX_SECONDS),
           "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "pipe:1"]
    try:
        proc = subprocess.run(cmd, input=data, capture_output=True, timeout=_FFMPEG_TIMEOUT, check=False)
    except FileNotFoundError as exc:
        raise AudioError("не найден ffmpeg — установи его: sudo dnf install ffmpeg") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioError("ffmpeg не успел разобрать запись") from exc
    if proc.returncode != 0 or not proc.stdout:
        detail = proc.stderr.decode("utf-8", "replace").strip()[-200:]
        raise AudioError(f"не удалось прочитать запись: {detail or 'нет звука'}")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def duration(samples: np.ndarray) -> float:
    return len(samples) / SAMPLE_RATE
