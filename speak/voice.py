"""Распознавание (faster-whisper) и голос собеседника (Piper). Модели грузятся при первом обращении."""
from __future__ import annotations

import io
import logging
import re
import threading
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

SLOW_LENGTH_SCALE = 1.25
_MARKUP = re.compile(r"[*_#`~>\[\]]")


class Transcriber:
    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            log.info("загружаю Whisper %s…", self._model_name)
            self._model = WhisperModel(self._model_name, device="cpu", compute_type="int8")
        return self._model

    def transcribe(self, samples: np.ndarray) -> str:
        with self._lock:
            model = self._load()
            # без initial_prompt и контекста: Whisper не должен «додумывать» правильную грамматику
            segments, _ = model.transcribe(samples, language="en", beam_size=1, vad_filter=True,
                                           condition_on_previous_text=False)
            return " ".join(s.text.strip() for s in segments).strip()


class Speaker:
    def __init__(self, voice_path: Path) -> None:
        self._voice_path = voice_path
        self._voice = None
        self._lock = threading.Lock()

    def _load(self):
        if self._voice is None:
            from piper import PiperVoice

            if not self._voice_path.exists():
                raise FileNotFoundError(f"нет голоса {self._voice_path.name} — запусти: speak setup")
            self._voice = PiperVoice.load(self._voice_path)
        return self._voice

    def synthesize(self, text: str, slow: bool = False) -> bytes:
        from piper import SynthesisConfig

        config = SynthesisConfig(length_scale=SLOW_LENGTH_SCALE if slow else 1.0)
        buf = io.BytesIO()
        with self._lock:
            voice = self._load()
            with wave.open(buf, "wb") as wav:
                voice.synthesize_wav(clean_for_speech(text), wav, syn_config=config)
        return buf.getvalue()


def clean_for_speech(text: str) -> str:
    """Убираем разметку, которую TTS прочитал бы вслух или споткнулся бы о неё."""
    return re.sub(r"\s+", " ", _MARKUP.sub("", text)).strip()
