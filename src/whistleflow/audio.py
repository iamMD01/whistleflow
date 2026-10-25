"""Microphone capture.

The stream is opened on trigger and closed on release, so the microphone is
never held open while idle (FR-2.4, FR-2.9, NFR-4).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

from .config import AudioConfig

log = logging.getLogger(__name__)


class AudioError(RuntimeError):
    """Raised when the microphone cannot be opened."""


class Recorder:
    """Captures mono float32 audio from the configured input device."""

    def __init__(self, cfg: AudioConfig, *, max_duration_s: int = 60) -> None:
        self._cfg = cfg
        self._max_duration_s = max_duration_s
        self._stream: sd.InputStream | None = None
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._rate = 44100
        self._total_samples = 0
        self._started_at = 0.0

    @property
    def rate(self) -> int:
        """Sample rate of the device the last recording was captured at."""
        return self._rate

    @property
    def duration_s(self) -> float:
        """Length of the current or most recent recording, in seconds."""
        if self._rate <= 0:
            return 0.0
        return self._total_samples / self._rate

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        """Open the input stream and begin capturing."""
        if self._stream is not None:
            return
        device = self._resolve_device()
        try:
            info = self._device_info(device)
            self._rate = int(info["default_samplerate"])
            self._total_samples = 0
            with self._lock:
                self._chunks = []
            stream = sd.InputStream(
                device=device,
                samplerate=self._rate,
                channels=1,
                dtype="float32",
                callback=self._callback,
            )
            stream.start()
        except Exception as exc:  # surfaced as a user facing error
            raise AudioError(f"could not open microphone: {exc}") from exc
        self._stream = stream
        self._started_at = time.monotonic()
        log.info("recording started: device=%r rate=%d Hz", info["name"], self._rate)

    def _callback(self, indata: np.ndarray, frames: int, time_info: object, status: object) -> None:
        """PortAudio callback: append a copy of the chunk and nothing else."""
        if status:
            log.warning("audio status: %s", status)
        chunk = np.array(indata, dtype=np.float32, copy=True).reshape(-1)
        with self._lock:
            self._chunks.append(chunk)
            self._total_samples += chunk.size

    def stop(self) -> np.ndarray:
        """Close the stream and return the captured mono float32 samples."""
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
            except Exception as exc:  # noqa: BLE001 - the stream is already gone
                log.debug("stopping the stream failed: %s", exc)
            try:
                stream.close()
            except Exception as exc:  # noqa: BLE001 - the stream is already gone
                log.debug("closing the stream failed: %s", exc)
        with self._lock:
            chunks = self._chunks
            self._chunks = []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32, copy=False)

    def cancel(self) -> None:
        """Discard whatever was captured."""
        self.stop()
        with self._lock:
            self._chunks = []
            self._total_samples = 0

    def _device_info(self, device: int | None) -> dict:
        """Details of a device. None means the system's default input."""
        if device is None:
            return dict(sd.query_devices(kind="input"))
        return dict(sd.query_devices(device))

    def _resolve_device(self) -> int | None:
        """Turn the configured device spec into a sounddevice device index."""
        spec = self._cfg.device.strip()
        if not spec or spec.lower() == "default":
            return None

        devices = sd.query_devices()
        inputs = [i for i, d in enumerate(devices) if d["max_input_channels"] > 0]
        if not inputs:
            raise AudioError("no input device available")

        if spec.lstrip("-").isdigit():
            index = int(spec)
            if index in inputs:
                return index
            raise AudioError(f"input device {index} not found")

        wanted = spec.lower()
        for index in inputs:
            if devices[index]["name"].lower() == wanted:
                return index
        for index in inputs:
            if wanted in devices[index]["name"].lower():
                return index
        raise AudioError(f"input device {spec!r} not found")


def save_debug_wav(path: Path, samples_16k: np.ndarray) -> Path:
    """Write a 16 kHz mono PCM16 WAV, used only when debug_save is on."""
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, np.asarray(samples_16k, dtype=np.float32), 16000, subtype="PCM_16")
    return path
