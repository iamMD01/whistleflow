"""Whistle model wrapper: resampling, transcription and text cleanup.

The engine is not thread-safe and loading the model is the dominant startup
cost, so a single :class:`Transcriber` instance is created at daemon start and
used from one worker thread only (FR-3.1, FR-3.2).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import numpy as np
import soxr

from .config import SttConfig

log = logging.getLogger(__name__)

TARGET_RATE = 16000

# FR-3.7: collapse every run of whitespace — including newlines and tabs — to
# a single space. A newline pasted into a terminal would execute a command.
_WHITESPACE_RUN = re.compile(r"\s+")


def to_16k(samples: np.ndarray, rate: int) -> np.ndarray:
    """Resample mono float32 audio to 16 kHz mono float32."""
    data = np.asarray(samples, dtype=np.float32).reshape(-1)
    if data.size == 0:
        return data
    if rate == TARGET_RATE:
        return data
    return soxr.resample(data, rate, TARGET_RATE, quality="HQ").astype(np.float32, copy=False)


class Transcriber:
    """Owns the loaded model and turns audio into clean text."""

    def __init__(self, cfg: SttConfig) -> None:
        self._cfg = cfg
        self._model: Any = None

    @property
    def loaded(self) -> bool:
        """Whether the model is already in memory."""
        return self._model is not None

    def warmup(self) -> None:
        """Load the model once so the first utterance is not slowed down."""
        if self._model is not None:
            return
        import needle

        started = time.perf_counter()
        self._model = needle.Whistle()
        log.info(
            "model loaded in %.0f ms (language=%s)",
            (time.perf_counter() - started) * 1000,
            self._cfg.language,
        )

    def transcribe(self, samples_16k: np.ndarray) -> dict[str, Any]:
        """Transcribe 16 kHz mono float32 audio."""
        if self._model is None:
            self.warmup()
        language = None if self._cfg.language == "auto" else self._cfg.language
        keywords = list(self._cfg.keywords) or None
        return self._model.transcribe(
            samples_16k,
            language=language,
            keywords=keywords,
            word_timestamps=False,
        )

    def postprocess(self, result: dict[str, Any]) -> str:
        """Strip and collapse whitespace, preserving punctuation and case."""
        text = result.get("text") or ""
        return _WHITESPACE_RUN.sub(" ", str(text)).strip()
