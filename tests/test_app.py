"""Unit tests for the daemon wiring around the state machine.

The recorder, the feedback and the injector are replaced by fakes, so the
recording lifecycle — including the maximum duration — is tested without a
microphone, an X server or the model (§13.1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from whistleflow.app import WhistleflowApp
from whistleflow.config import load_config
from whistleflow.hotkey import Event
from whistleflow.state import Action, State


class FakeRecorder:
    """A microphone that only knows how to hand back a fixed buffer."""

    def __init__(self, samples: np.ndarray, rate: int = 44100) -> None:
        self._samples = samples
        self.rate = rate
        self.recording = False
        self.duration_s = 0.0
        self.cancelled = False

    def start(self) -> None:
        self.recording = True

    def stop(self) -> np.ndarray:
        self.recording = False
        self.duration_s = self._samples.size / self.rate
        return self._samples

    def cancel(self) -> None:
        self.recording = False
        self.cancelled = True
        self._samples = np.zeros(0, dtype=np.float32)


class FakeFeedback:
    """Records the feedback events the app emits."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def start(self) -> None:
        self.events.append("start")

    def stop(self) -> None:
        self.events.append("stop")

    def cancel(self) -> None:
        self.events.append("cancel")

    def error(self, message: str) -> None:
        self.events.append(f"error: {message}")


class FakeTranscriber:
    """A model stub that returns a canned transcript."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = 0

    def transcribe(self, samples_16k: np.ndarray) -> dict[str, Any]:
        self.calls += 1
        return {"text": self._text}

    def postprocess(self, result: dict[str, Any]) -> str:
        import re

        return re.sub(r"\s+", " ", str(result.get("text") or "")).strip()


class FakeInjector:
    """Records what would have been pasted."""

    def __init__(self) -> None:
        self.pasted: list[str] = []

    def paste(self, text: str) -> None:
        self.pasted.append(text)


@pytest.fixture
def app(tmp_path: Path) -> WhistleflowApp:
    """A daemon with default settings; no hardware is touched."""
    return WhistleflowApp(load_config(tmp_path / "missing.toml"))


def _one_second() -> np.ndarray:
    return np.ones(44100, dtype=np.float32)


# ------------------------------------------------------------- release to paste


def test_releasing_the_chord_queues_the_capture(app: WhistleflowApp) -> None:
    """FR-1.3 / FR-2.4: the release stops the stream and hands over the audio."""
    app._recorder = FakeRecorder(_one_second())  # type: ignore[assignment]
    app._feedback = FakeFeedback()  # type: ignore[assignment]

    app._sm.on_key_down("alt")
    app._sm.on_key_down("l")
    action = app._sm.on_key_up("alt")
    assert action is Action.STOP_RECORDING

    app._on_event(Event(action=action, key="alt"))

    assert app._sm.state is State.BUSY
    assert app._jobs.qsize() == 1
    assert app._feedback.events == ["stop"]  # type: ignore[attr-defined]


def test_a_recording_shorter_than_the_minimum_is_discarded(app: WhistleflowApp) -> None:
    """FR-2.5: a short tap is dropped silently, nothing is transcribed."""
    app._recorder = FakeRecorder(np.ones(4410, dtype=np.float32))  # type: ignore[assignment]
    app._feedback = FakeFeedback()  # type: ignore[assignment]

    app._sm.on_key_down("alt")
    app._sm.on_key_down("l")
    app._on_event(Event(action=app._sm.on_key_up("l"), key="l"))

    assert app._jobs.qsize() == 0
    assert app._sm.state is State.IDLE
    assert app._feedback.events == ["stop"]  # type: ignore[attr-defined]


def test_an_empty_capture_is_discarded(app: WhistleflowApp) -> None:
    """FR-2.7: zero samples are an empty result, not a transcription."""
    app._recorder = FakeRecorder(np.zeros(0, dtype=np.float32))  # type: ignore[assignment]
    app._feedback = FakeFeedback()  # type: ignore[assignment]

    app._sm.on_key_down("alt")
    app._sm.on_key_down("l")
    app._on_event(Event(action=app._sm.on_key_up("alt"), key="alt"))

    assert app._jobs.qsize() == 0
    assert app._sm.state is State.IDLE


def test_the_worker_returns_the_daemon_to_idle(app: WhistleflowApp) -> None:
    """§7.3: BUSY ends when the worker is done."""
    app._recorder = FakeRecorder(_one_second())  # type: ignore[assignment]
    app._feedback = FakeFeedback()  # type: ignore[assignment]

    app._sm.on_key_down("alt")
    app._sm.on_key_down("l")
    app._on_event(Event(action=app._sm.on_key_up("alt"), key="alt"))
    assert app._sm.state is State.BUSY

    app._sm.finish_busy()
    assert app._sm.state is State.IDLE


# ------------------------------------------------------------- max duration


def test_max_duration_stops_the_capture_and_queues_it(app: WhistleflowApp) -> None:
    """FR-2.6: the recording is finalised at the limit and processed."""
    recorder = FakeRecorder(_one_second())
    recorder.recording = True
    app._recorder = recorder  # type: ignore[assignment]
    app._feedback = FakeFeedback()  # type: ignore[assignment]

    app._sm.on_key_down("alt")
    app._sm.on_key_down("l")
    app._on_max_duration()

    assert recorder.recording is False
    assert app._jobs.qsize() == 1
    assert app._feedback.events == ["stop"]  # type: ignore[attr-defined]


def test_max_duration_moves_the_state_to_busy(app: WhistleflowApp) -> None:
    """§7.3: the duration limit ends the recording and moves the daemon to BUSY."""
    recorder = FakeRecorder(_one_second())
    recorder.recording = True
    app._recorder = recorder  # type: ignore[assignment]
    app._feedback = FakeFeedback()  # type: ignore[assignment]

    app._sm.on_key_down("alt")
    app._sm.on_key_down("l")
    app._on_max_duration()

    assert app._sm.state is State.BUSY


def test_releasing_the_key_after_the_auto_stop_does_not_stop_again(app: WhistleflowApp) -> None:
    """The auto-stop owns the end of the recording; a later release is a no-op."""
    recorder = FakeRecorder(_one_second())
    recorder.recording = True
    app._recorder = recorder  # type: ignore[assignment]
    app._feedback = FakeFeedback()  # type: ignore[assignment]

    app._sm.on_key_down("alt")
    app._sm.on_key_down("l")
    app._on_max_duration()

    assert app._sm.on_key_up("alt") is Action.NONE
    assert app._jobs.qsize() == 1
    assert app._feedback.events == ["stop"]  # type: ignore[attr-defined]


def test_max_duration_does_nothing_when_nothing_is_recording(app: WhistleflowApp) -> None:
    """The timer may fire after the recording already ended; it must be a no-op."""
    recorder = FakeRecorder(_one_second())
    recorder.recording = False
    app._recorder = recorder  # type: ignore[assignment]
    app._feedback = FakeFeedback()  # type: ignore[assignment]

    app._on_max_duration()
    assert app._jobs.qsize() == 0
    assert app._feedback.events == []  # type: ignore[attr-defined]


# ------------------------------------------------------------------ processing


def test_the_transcript_is_pasted(app: WhistleflowApp) -> None:
    """The worker pastes the cleaned-up transcript at the current focus."""
    app._recorder = FakeRecorder(_one_second())  # type: ignore[assignment]
    app._transcriber = FakeTranscriber("  hello   world \n")  # type: ignore[assignment]
    app._injector = FakeInjector()  # type: ignore[assignment]
    app._feedback = FakeFeedback()  # type: ignore[assignment]

    app._process(_one_second())

    assert app._injector.pasted == ["hello world"]  # type: ignore[attr-defined]


def test_an_empty_transcript_is_not_pasted(app: WhistleflowApp) -> None:
    """FR-3.8: an empty transcript produces a notification, not a paste."""
    app._recorder = FakeRecorder(_one_second())  # type: ignore[assignment]
    app._transcriber = FakeTranscriber("   ")  # type: ignore[assignment]
    app._injector = FakeInjector()  # type: ignore[assignment]
    app._feedback = FakeFeedback()  # type: ignore[assignment]

    app._process(_one_second())

    assert app._injector.pasted == []  # type: ignore[attr-defined]
    assert app._feedback.events == ["error: No speech detected"]  # type: ignore[attr-defined]
