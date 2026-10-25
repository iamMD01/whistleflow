"""Text injection: clipboard plus a synthetic paste keystroke.

Clipboard paste works in every app that accepts it and needs a single
``xdotool`` call instead of hundreds of synthetic key events (§8.4).
"""

from __future__ import annotations

import logging
import re
import subprocess
import time

from .config import InjectConfig

log = logging.getLogger(__name__)

SUBPROCESS_TIMEOUT = 2.0

# xclip forks a child that owns the selection; give it a moment to take over
# before the paste keystroke is sent (§8.8).
CLIPBOARD_SETTLE_S = 0.1

_QUOTED = re.compile(r'"([^"]*)"')


class InjectError(RuntimeError):
    """Raised when the text could not be pasted into the focused window."""


def parse_wm_class(output: str) -> str | None:
    """Extract the window class from ``xprop -id <id> WM_CLASS`` output.

    The property holds two strings, instance name then class name; the class
    is the second one (§8.7).
    """
    if not output:
        return None
    _, separator, value = output.partition("=")
    if not separator:
        value = output
    parts = _QUOTED.findall(value)
    if len(parts) >= 2:
        return parts[1]
    if len(parts) == 1:
        return parts[0]
    return None


class Injector:
    """Puts text on the clipboard and pastes it into the focused window."""

    def __init__(self, cfg: InjectConfig) -> None:
        self._cfg = cfg

    def paste(self, text: str) -> None:
        """Copy the text to the clipboard and paste it. Raises InjectError."""
        if not text:
            raise InjectError("refusing to paste empty text")
        payload = text
        if self._cfg.trailing_space and not payload[-1].isspace():
            payload += " "

        self._set_clipboard(payload)
        time.sleep(CLIPBOARD_SETTLE_S)
        self._send_paste_key(self.paste_key())

    def paste_key(self) -> str:
        """The paste shortcut to use, honouring per-window-class overrides."""
        if not self._cfg.terminal_overrides:
            # Without overrides there is nothing to look up, so skip the two
            # extra subprocesses and keep the paste path cheap.
            return self._cfg.paste_key
        window_class = self.active_window_class()
        if window_class:
            override = self._cfg.terminal_overrides.get(window_class.lower())
            if override:
                log.debug("using paste key %r for window class %r", override, window_class)
                return override
        return self._cfg.paste_key

    def active_window_class(self) -> str | None:
        """The WM_CLASS of the window that currently has focus."""
        window_id = self._active_window_id()
        if window_id is None:
            return None
        result = self._run(["xprop", "-id", window_id, "WM_CLASS"])
        if result is None or result.returncode != 0:
            return None
        return parse_wm_class(result.stdout)

    def _active_window_id(self) -> str | None:
        result = self._run(["xdotool", "getactivewindow"])
        if result is None or result.returncode != 0:
            return None
        window_id = result.stdout.strip()
        if not window_id.isdigit():
            return None
        return window_id

    def _set_clipboard(self, payload: str) -> None:
        # xclip forks a child that keeps serving the X selection, and that
        # child inherits stdout and stderr. Capturing them would keep the pipes
        # open forever, so subprocess.run would block until the timeout. Both
        # streams therefore go to /dev/null and only the exit code is checked.
        try:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-i"],
                input=payload,
                text=True,
                timeout=SUBPROCESS_TIMEOUT,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            raise InjectError("xclip timed out while setting the clipboard") from None
        except OSError as exc:
            raise InjectError(f"could not run xclip: {exc}") from None
        if result.returncode != 0:
            raise InjectError(f"xclip exited with {result.returncode}")

    def _send_paste_key(self, key: str) -> None:
        try:
            result = subprocess.run(
                ["xdotool", "key", "--clearmodifiers", key],
                timeout=SUBPROCESS_TIMEOUT,
                check=False,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired:
            raise InjectError("xdotool timed out while pasting") from None
        except OSError as exc:
            raise InjectError(f"could not run xdotool: {exc}") from None
        if result.returncode != 0:
            raise InjectError(f"xdotool exited with {result.returncode}")

    @staticmethod
    def _run(argv: list[str]) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                argv,
                timeout=SUBPROCESS_TIMEOUT,
                check=False,
                capture_output=True,
                text=True,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.debug("%s failed: %s", argv[0], exc)
            return None
