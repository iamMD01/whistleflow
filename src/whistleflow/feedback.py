"""Beeps and desktop notifications.

The daemon is invisible by design, so it talks to the user through short
sounds and ``notify-send`` messages (§4.2). Every call is best-effort: a failed
beep must never take down an utterance (FR-5.4).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .config import FeedbackConfig

log = logging.getLogger(__name__)

SUBPROCESS_TIMEOUT = 2.0

SOUND_DIR = Path("/usr/share/sounds/freedesktop/stereo")
START_SOUND = SOUND_DIR / "message-new-instant.oga"
STOP_SOUND = SOUND_DIR / "message.oga"
ERROR_SOUND = SOUND_DIR / "dialog-error.oga"
CANCEL_SOUND = SOUND_DIR / "bell.oga"

APP_NAME = "Whistleflow"


class Feedback:
    """Plays beeps and raises notifications according to the feedback mode."""

    def __init__(self, cfg: FeedbackConfig) -> None:
        self._cfg = cfg

    @property
    def mode(self) -> str:
        return self._cfg.mode

    def start(self) -> None:
        """Recording started."""
        if self._cfg.mode == "beep":
            self._beep(START_SOUND)
        elif self._cfg.mode == "notify":
            self.notify("Recording")

    def stop(self) -> None:
        """Recording stopped, transcription is about to start."""
        if self._cfg.mode == "beep":
            self._beep(STOP_SOUND)
        elif self._cfg.mode == "notify":
            self.notify("Transcribing")

    def cancel(self) -> None:
        """The utterance was cancelled."""
        if self._cfg.mode == "beep":
            self._beep(CANCEL_SOUND)
        elif self._cfg.mode == "notify":
            self.notify("Cancelled")

    def error(self, message: str) -> None:
        """Something went wrong; the user needs to know (FR-5.5)."""
        if self._cfg.mode == "beep":
            self._beep(ERROR_SOUND)
        elif self._cfg.mode == "notify":
            pass
        else:
            return
        self.notify(message)

    def notify(self, message: str) -> None:
        """Show a desktop notification. Never raises."""
        try:
            result = subprocess.run(
                ["notify-send", "-a", APP_NAME, APP_NAME, message],
                timeout=SUBPROCESS_TIMEOUT,
                check=False,
                capture_output=True,
                text=True,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.debug("notify-send failed: %s", exc)
            return
        if result.returncode != 0:
            log.debug("notify-send exited with %s", result.returncode)

    def _beep(self, sound: Path) -> None:
        """Fire and forget: a beep must never block the recording (FR-5.4)."""
        if not sound.exists():
            log.debug("sound file missing: %s", sound)
            return
        try:
            subprocess.Popen(
                ["paplay", f"--volume={self._cfg.volume}", str(sound)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            log.warning("paplay failed: %s", exc)
