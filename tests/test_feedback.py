"""Unit tests for the beeps and the desktop notifications.

``paplay`` and ``notify-send`` are mocked: no sound server, no desktop
session (§13.1, FR-5.4).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from whistleflow import feedback as feedback_module
from whistleflow.config import FeedbackConfig
from whistleflow.feedback import Feedback


class FakePopen:
    """Records the ``paplay`` invocations."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, argv: list[str], **kwargs: object) -> None:
        self.calls.append((list(argv), kwargs))


class FakeRun:
    """Records the ``notify-send`` invocations."""

    def __init__(self, result: subprocess.CompletedProcess[str] | Exception | None = None) -> None:
        self.result = result
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(argv), kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result or subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def sounds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    """Point the sound constants at files that exist in a temporary directory."""
    made = {}
    for name in ("START_SOUND", "STOP_SOUND", "ERROR_SOUND", "CANCEL_SOUND"):
        path = tmp_path / f"{name.lower()}.oga"
        path.write_bytes(b"sound")
        made[name] = path
        monkeypatch.setattr(feedback_module, name, path)
    return made


@pytest.fixture
def popen(monkeypatch: pytest.MonkeyPatch) -> FakePopen:
    """Replace ``subprocess.Popen`` so no sound is ever played."""
    fake = FakePopen()
    monkeypatch.setattr(feedback_module.subprocess, "Popen", fake)
    return fake


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    """Replace ``subprocess.run`` so no notification is ever shown."""
    fake = FakeRun()
    monkeypatch.setattr(feedback_module.subprocess, "run", fake)
    return fake


# ------------------------------------------------------------------- beep mode


def test_the_start_beep_plays_the_start_sound(sounds: dict[str, Path], popen: FakePopen) -> None:
    """FR-5.1: recording starts with a quiet beep."""
    Feedback(FeedbackConfig(mode="beep")).start()

    assert len(popen.calls) == 1
    assert popen.calls[0][0][0] == "paplay"
    assert popen.calls[0][0][-1] == str(sounds["START_SOUND"])


def test_the_beep_carries_the_configured_volume(sounds: dict[str, Path], popen: FakePopen) -> None:
    """The volume from the config is handed to paplay."""
    Feedback(FeedbackConfig(mode="beep", volume=1234)).start()

    assert popen.calls[0][0][1] == "--volume=1234"


def test_the_stop_and_cancel_beeps_use_different_sounds(
    sounds: dict[str, Path], popen: FakePopen
) -> None:
    """The user must be able to tell the three states apart by ear."""
    feedback = Feedback(FeedbackConfig(mode="beep"))
    feedback.start()
    feedback.stop()
    feedback.cancel()

    played = [call[0][-1] for call in popen.calls]
    assert played == [
        str(sounds["START_SOUND"]),
        str(sounds["STOP_SOUND"]),
        str(sounds["CANCEL_SOUND"]),
    ]


def test_a_missing_sound_file_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, popen: FakePopen
) -> None:
    """A missing .oga must not raise; the beep is simply skipped."""
    monkeypatch.setattr(feedback_module, "START_SOUND", tmp_path / "does-not-exist.oga")

    Feedback(FeedbackConfig(mode="beep")).start()
    assert popen.calls == []


def test_a_failing_paplay_does_not_raise(
    sounds: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-5.4: a beep must never take down the daemon."""

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("no sound server")

    monkeypatch.setattr(feedback_module.subprocess, "Popen", boom)

    Feedback(FeedbackConfig(mode="beep")).start()


def test_the_beep_is_detached_from_the_daemon(sounds: dict[str, Path], popen: FakePopen) -> None:
    """FR-5.4: paplay runs in its own session so it cannot block the daemon."""
    Feedback(FeedbackConfig(mode="beep")).start()

    _argv, kwargs = popen.calls[0]
    assert kwargs["start_new_session"] is True


# ----------------------------------------------------------------- notify mode


def test_notify_mode_sends_a_notification(run: FakeRun, popen: FakePopen) -> None:
    """FR-5.6: the "notify" mode replaces the beeps with notifications."""
    Feedback(FeedbackConfig(mode="notify")).start()

    assert popen.calls == []
    assert run.calls[0][0] == ["notify-send", "-a", "Whistleflow", "Whistleflow", "Recording"]


def test_the_stop_notification_says_transcribing(run: FakeRun) -> None:
    """The state change is spelled out for the user."""
    Feedback(FeedbackConfig(mode="notify")).stop()

    assert run.calls[0][0][-1] == "Transcribing"


def test_the_cancel_notification_says_cancelled(run: FakeRun) -> None:
    """A cancelled utterance is confirmed with a notification."""
    Feedback(FeedbackConfig(mode="notify")).cancel()

    assert run.calls[0][0][-1] == "Cancelled"


def test_a_notification_has_a_timeout(run: FakeRun) -> None:
    """§12: every subprocess call carries a timeout."""
    Feedback(FeedbackConfig(mode="notify")).start()

    _argv, kwargs = run.calls[0]
    assert kwargs["timeout"] == feedback_module.SUBPROCESS_TIMEOUT


def test_a_failing_notify_send_does_not_raise(
    sounds: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """§12: a notify-send failure is logged only."""
    monkeypatch.setattr(
        feedback_module.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, "", "boom"),
    )

    Feedback(FeedbackConfig(mode="notify")).start()


def test_a_missing_notify_send_does_not_raise(
    sounds: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """notify-send may not be installed at all; the daemon keeps running."""

    def boom(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("notify-send")

    monkeypatch.setattr(feedback_module.subprocess, "run", boom)

    Feedback(FeedbackConfig(mode="notify")).start()


def test_a_notify_send_timeout_does_not_raise(
    sounds: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hanging notify-send must not block the daemon."""

    def boom(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="notify-send", timeout=2.0)

    monkeypatch.setattr(feedback_module.subprocess, "run", boom)

    Feedback(FeedbackConfig(mode="notify")).start()


# ------------------------------------------------------------------- errors


def test_an_error_beeps_and_notifies(
    sounds: dict[str, Path], popen: FakePopen, run: FakeRun
) -> None:
    """FR-5.3 / FR-5.5: an error gets both an audible and a visible signal."""
    Feedback(FeedbackConfig(mode="beep")).error("Microphone unavailable: boom")

    assert popen.calls[0][0][-1] == str(sounds["ERROR_SOUND"])
    assert run.calls[0][0][-1] == "Microphone unavailable: boom"


def test_an_error_in_notify_mode_only_notifies(
    sounds: dict[str, Path], popen: FakePopen, run: FakeRun
) -> None:
    """In "notify" mode the error is a notification, without a beep."""
    Feedback(FeedbackConfig(mode="notify")).error("No speech detected")

    assert popen.calls == []
    assert run.calls[0][0][-1] == "No speech detected"


def test_an_error_in_none_mode_is_silent(
    sounds: dict[str, Path], popen: FakePopen, run: FakeRun
) -> None:
    """FR-5.6: the "none" mode stays completely silent."""
    Feedback(FeedbackConfig(mode="none")).error("No speech detected")

    assert popen.calls == []
    assert run.calls == []


# -------------------------------------------------------------------- none mode


def test_none_mode_is_completely_silent(
    sounds: dict[str, Path], popen: FakePopen, run: FakeRun
) -> None:
    """FR-5.6: the "none" mode produces no output at all."""
    feedback = Feedback(FeedbackConfig(mode="none"))
    feedback.start()
    feedback.stop()
    feedback.cancel()
    feedback.error("boom")

    assert popen.calls == []
    assert run.calls == []


def test_the_mode_is_exposed(sounds: dict[str, Path]) -> None:
    """The mode is readable, so the app can adapt to it."""
    assert Feedback(FeedbackConfig(mode="notify")).mode == "notify"
