import shutil

import pytest

from speak import __main__ as cli
from speak import audio
from speak.config import Settings, load_settings
from speak.voice import Speaker, Transcriber, to_word

REAL = load_settings()
needs_models = pytest.mark.skipif(
    not REAL.voice_path.exists() or shutil.which("ffmpeg") is None,
    reason="нужны скачанные модели: speak setup")


def test_settings_read_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("SPEAK_HOME", str(tmp_path))
    monkeypatch.setenv("SPEAK_MODEL", "haiku")
    monkeypatch.setenv("SPEAK_VOICE", "en_GB-alba-medium")
    settings = load_settings()
    assert settings.claude_model == "haiku"
    assert settings.voice_path == tmp_path / "voices" / "en_GB-alba-medium.onnx"
    assert settings.sessions_dir == tmp_path / "sessions"


def _empty_settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path, claude_model="sonnet", whisper_model="base.en", voice="none")


def test_check_and_serve_report_missing_voice(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_settings", lambda: _empty_settings(tmp_path))
    assert cli.main(["check"]) == 1
    assert cli.main(["serve", "--no-browser"]) == 1


def test_serve_explains_busy_port(tmp_path, monkeypatch):
    settings = _empty_settings(tmp_path)
    settings.voices_dir.mkdir(parents=True)
    settings.voice_path.write_bytes(b"")

    def busy(*args):
        raise OSError(98, "Address already in use")

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "serve", busy)
    assert cli.main(["serve", "--no-browser"]) == 1


def test_speaker_without_voice_explains_setup(tmp_path):
    with pytest.raises(FileNotFoundError, match="speak setup"):
        Speaker(tmp_path / "missing.onnx").synthesize("Hi")


@needs_models
def test_round_trip_synthesis_is_recognised_with_word_timings():
    """Голос Piper → ffmpeg → Whisper: текст совпадает, у слов есть тайминги и уверенность."""
    wav = Speaker(REAL.voice_path).synthesize("Hello, how was your weekend?", slow=True)
    transcript = Transcriber("base.en").transcribe(audio.decode(wav))
    assert "weekend" in transcript.text.lower() and "hello" in transcript.text.lower()
    words = [w.text.lower() for w in transcript.words]
    assert words == ["hello", "how", "was", "your", "weekend"]  # пунктуация срезана
    assert all(0 <= w.start < w.end for w in transcript.words)
    assert all(not w.unsure for w in transcript.words)  # чёткий синтетический голос — уверенно
    assert all(isinstance(w.public()["start"], float) for w in transcript.words)


def test_to_word_strips_punctuation_and_numpy_types():
    import numpy as np

    word = to_word(" weekend?", np.float32(1.25), np.float32(1.75), np.float32(0.4))
    assert word.text == "weekend" and word.unsure and type(word.start) is float
    assert to_word(" ...", 0, 1, 0.9) is None
    assert to_word("don't,", 0, 1, 0.9).text == "don't"
