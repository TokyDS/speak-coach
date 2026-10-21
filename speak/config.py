"""Пути и настройки. Всё переопределяется переменными окружения SPEAK_*."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    claude_model: str
    whisper_model: str
    voice: str

    @property
    def voices_dir(self) -> Path:
        return self.data_dir / "voices"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def phrasebook_path(self) -> Path:
        return self.data_dir / "phrasebook.json"

    @property
    def voice_path(self) -> Path:
        return self.voices_dir / f"{self.voice}.onnx"


def load_settings() -> Settings:
    default_home = Path.home() / ".local" / "share" / "speak-coach"
    return Settings(
        data_dir=Path(os.environ.get("SPEAK_HOME", default_home)),
        claude_model=os.environ.get("SPEAK_MODEL", "sonnet"),
        whisper_model=os.environ.get("SPEAK_WHISPER", "small.en"),
        voice=os.environ.get("SPEAK_VOICE", "en_US-lessac-medium"),
    )
