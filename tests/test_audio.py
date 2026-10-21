import shutil

import pytest

from speak import audio
from speak.voice import clean_for_speech

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="нужен ffmpeg")


@needs_ffmpeg
def test_decode_returns_16k_float_samples(tone_wav):
    samples = audio.decode(tone_wav)
    assert samples.dtype.name == "float32"
    assert audio.duration(samples) == pytest.approx(1.0, abs=0.05)
    assert 0.1 < float(abs(samples).max()) <= 1.0


@needs_ffmpeg
def test_decode_rejects_garbage():
    with pytest.raises(audio.AudioError, match="не удалось прочитать"):
        audio.decode(b"definitely not audio")


def test_decode_rejects_empty():
    with pytest.raises(audio.AudioError, match="пустая"):
        audio.decode(b"")


def test_clean_for_speech_strips_markup():
    assert clean_for_speech("**Nice!**  That's   _great_ [laughs]") == "Nice! That's great laughs"
