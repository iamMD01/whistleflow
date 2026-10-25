"""Unit tests for the resampling helper and the transcript clean-up.

No model is loaded here: only the pure functions are exercised (§13.1).
"""

from __future__ import annotations

import numpy as np
import pytest

from whistleflow.config import SttConfig
from whistleflow.stt import TARGET_RATE, Transcriber, to_16k


def _sine(freq_hz: float, rate: int, seconds: float) -> np.ndarray:
    """A mono float32 sine wave — the cheapest signal to check fidelity."""
    t = np.arange(int(rate * seconds), dtype=np.float64) / rate
    return np.sin(2 * np.pi * freq_hz * t).astype(np.float32)


def _dominant_frequency(samples: np.ndarray, rate: int) -> float:
    """The strongest FFT bin, in Hz — used to prove the wave survived."""
    spectrum = np.abs(np.fft.rfft(samples.astype(np.float64)))
    return float(np.argmax(spectrum)) * rate / len(samples)


# ------------------------------------------------------------------ to_16k


def test_empty_input_returns_an_empty_array() -> None:
    """An empty capture must not blow up the resampler."""
    out = to_16k(np.zeros(0, dtype=np.float32), 44100)
    assert out.size == 0
    assert out.dtype == np.float32


def test_output_length_encodes_the_target_rate() -> None:
    """The output rate is inferred from the length: 1 s of 44.1 kHz → 16 000."""
    one_second = np.zeros(44100, dtype=np.float32)
    out = to_16k(one_second, 44100)

    assert out.size == 16000
    assert out.size == TARGET_RATE


def test_output_length_scales_with_the_input_rate() -> None:
    """The same duration in samples yields the same duration in seconds."""
    assert to_16k(np.zeros(8000, dtype=np.float32), 8000).size == 16000
    assert to_16k(np.zeros(48000, dtype=np.float32), 48000).size == 16000


def test_resampling_preserves_the_sine_wave() -> None:
    """A 440 Hz tone must still be a 440 Hz tone at 16 kHz."""
    out = to_16k(_sine(440.0, 44100, 0.5), 44100)

    assert _dominant_frequency(out, TARGET_RATE) == pytest.approx(440.0, abs=5.0)
    assert float(np.abs(out).max()) == pytest.approx(1.0, abs=0.02)


def test_resampling_keeps_the_dtype_and_shape_flat() -> None:
    """The model wants mono float32 — the helper guarantees that."""
    out = to_16k(_sine(220.0, 48000, 0.25).reshape(-1, 1), 48000)

    assert out.dtype == np.float32
    assert out.ndim == 1


def test_input_at_the_target_rate_is_passed_through() -> None:
    """16 kHz input is already correct and must not be resampled."""
    samples = _sine(440.0, TARGET_RATE, 0.25)
    out = to_16k(samples, TARGET_RATE)

    assert np.array_equal(out, samples)


def test_integer_input_is_converted_to_float32() -> None:
    """Whatever the device hands over, the result is float32."""
    out = to_16k(np.arange(1000, dtype=np.int16), 16000)
    assert out.dtype == np.float32


# -------------------------------------------------------------- postprocess


@pytest.fixture
def transcriber() -> Transcriber:
    """A transcriber without a model: postprocess does not need one."""
    return Transcriber(SttConfig())


def test_postprocess_collapses_whitespace_runs(transcriber: Transcriber) -> None:
    """FR-3.7: every run of whitespace becomes a single space."""
    assert transcriber.postprocess({"text": "hello   world"}) == "hello world"


def test_postprocess_collapses_newlines_and_tabs(transcriber: Transcriber) -> None:
    """FR-3.7 safety rule: a newline pasted into a terminal would run a command."""
    text = "first line\nsecond line\tthird\r\nfourth"
    assert transcriber.postprocess({"text": text}) == "first line second line third fourth"


def test_postprocess_strips_leading_and_trailing_whitespace(transcriber: Transcriber) -> None:
    """The result must be clean at both ends."""
    assert transcriber.postprocess({"text": "  \n hello world \t "}) == "hello world"


def test_postprocess_of_an_empty_result_is_empty(transcriber: Transcriber) -> None:
    """FR-3.8: an empty result must not be injected."""
    assert transcriber.postprocess({"text": ""}) == ""
    assert transcriber.postprocess({"text": "   \n\t "}) == ""


def test_postprocess_of_a_result_without_text_is_empty(transcriber: Transcriber) -> None:
    """A missing key must not raise."""
    assert transcriber.postprocess({}) == ""


def test_postprocess_of_a_none_text_is_empty(transcriber: Transcriber) -> None:
    """A None text must not raise."""
    assert transcriber.postprocess({"text": None}) == ""


def test_postprocess_preserves_punctuation_and_case(transcriber: Transcriber) -> None:
    """Only whitespace is touched — the wording must survive verbatim."""
    text = 'Dr. Smith said: "Don\'t Panic!" (really)'
    assert transcriber.postprocess({"text": text}) == text


def test_postprocess_preserves_unicode(transcriber: Transcriber) -> None:
    """NFR-9: UTF-8 text must survive the clean-up untouched."""
    text = "Grüße, café — 你好 🎙️"
    assert transcriber.postprocess({"text": text}) == text


def test_postprocess_keeps_single_spaces_between_words(transcriber: Transcriber) -> None:
    """A normal sentence is returned unchanged."""
    assert transcriber.postprocess({"text": "one two three"}) == "one two three"
