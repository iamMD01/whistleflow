"""Global hotkey listener.

Wraps pynput's keyboard listener and feeds normalised key events into the
state machine. The listener is passive on purpose: the focused application
still receives the keystroke, so a daemon bug can never make Alt+L unusable
(FR-1.7, §8.2).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .state import Action, StateMachine, normalize_key

log = logging.getLogger(__name__)

# Autostart can race the X session, so retry the connection for a while.
X_CONNECT_TIMEOUT_S = 5.0
X_CONNECT_RETRY_S = 0.5


class HotkeyError(RuntimeError):
    """Raised when the X display cannot be reached."""


@dataclass(frozen=True)
class Event:
    """A state machine decision, delivered to the app."""

    action: Action
    key: str | None = None
    at: float = 0.0


def normalize_pynput_key(key: object) -> str | None:
    """Map a pynput key object onto a canonical key name.

    ``Key`` is an enum and always carries a name. ``KeyCode`` has a character
    for keys that map to one, and pynput's ``NamedKeyCode`` subclass adds a
    name for the special keys (space, enter, ...). A plain ``KeyCode`` for
    anything else — XF86 media keys, some function keys — has neither, and is
    not part of any chord, so it is ignored.
    """
    from pynput.keyboard import Key, KeyCode

    if isinstance(key, Key):
        return normalize_key(key.name)
    if isinstance(key, KeyCode):
        if key.char:
            return normalize_key(key.char)
        name = getattr(key, "name", None)
        if name:
            return normalize_key(name)
        return None
    return None


class HotkeyListener:
    """Listens for the trigger chord and reports actions to the app."""

    def __init__(self, trigger: str, sm: StateMachine, on_event: Callable[[Event], None]) -> None:
        self._trigger = trigger
        self._sm = sm
        self._on_event = on_event
        self._listener: object | None = None

    @property
    def running(self) -> bool:
        listener = self._listener
        return bool(listener is not None and getattr(listener, "is_alive", lambda: False)())

    def start(self) -> None:
        """Connect to the X display and start listening."""
        from pynput import keyboard

        deadline = time.monotonic() + X_CONNECT_TIMEOUT_S
        last_error: object = None
        while time.monotonic() < deadline:
            try:
                listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
                listener.start()
                time.sleep(0.2)
                if listener.is_alive():
                    self._listener = listener
                    log.info("listening for %r", self._trigger)
                    return
                last_error = "listener exited straight away"
            except Exception as exc:  # noqa: BLE001 - X11 errors vary by backend
                last_error = exc
            time.sleep(X_CONNECT_RETRY_S)
        raise HotkeyError(f"could not connect to the X display: {last_error}")

    def stop(self) -> None:
        """Stop listening."""
        listener = self._listener
        self._listener = None
        if listener is None:
            return
        try:
            listener.stop()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - the listener may already be gone
            log.debug("stopping the listener failed: %s", exc)

    def _on_press(self, key: object) -> None:
        # Everything is inside the try block: an exception escaping into the
        # pynput callback thread would kill the listener, and the daemon would
        # silently stop reacting to the hotkey.
        try:
            name = normalize_pynput_key(key)
            if name is None:
                return
            action = self._sm.on_key_down(name)
        except Exception:  # a callback must never kill the thread
            log.exception("key press handling failed")
            return
        self._emit(action, name)

    def _on_release(self, key: object) -> None:
        try:
            name = normalize_pynput_key(key)
            if name is None:
                return
            action = self._sm.on_key_up(name)
        except Exception:  # a callback must never kill the thread
            log.exception("key release handling failed")
            return
        self._emit(action, name)

    def _emit(self, action: Action, key: str) -> None:
        if action is Action.NONE:
            return
        log.debug("action=%s key=%s", action.value, key)
        self._on_event(Event(action=action, key=key, at=time.monotonic()))

    def join(self, timeout: float | None = None) -> None:
        """Wait for the listener thread to finish (used on shutdown)."""
        listener = self._listener
        if isinstance(listener, threading.Thread):
            listener.join(timeout)
