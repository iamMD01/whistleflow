"""The trigger state machine.

Pure logic: no threads, no I/O, no pynput. It takes normalised key names and
returns the action the app should perform, so every transition is unit-testable
without a keyboard (FR-1, §13.1).
"""

from __future__ import annotations

from enum import StrEnum

from .config import TriggerConfig


class State(StrEnum):
    """Where the daemon is in the push-to-talk cycle."""

    IDLE = "idle"
    RECORDING = "recording"
    BUSY = "busy"


class Action(StrEnum):
    """What the app should do next."""

    NONE = "none"
    START_RECORDING = "start_recording"
    STOP_RECORDING = "stop_recording"
    CANCEL_RECORDING = "cancel_recording"


# Spelling variants for modifier and cancel keys. The hotkey layer normalises
# pynput names ("alt_l", "cmd_l") and the config layer normalises user config
# ("Alt", "escape") through the same table.
_KEY_ALIASES = {
    "alt": "alt",
    "alt_l": "alt",
    "alt_r": "alt",
    "option": "alt",
    "ctrl": "ctrl",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "control": "ctrl",
    "shift": "shift",
    "shift_l": "shift",
    "shift_r": "shift",
    "super": "super",
    "super_l": "super",
    "super_r": "super",
    "cmd": "super",
    "cmd_l": "super",
    "cmd_r": "super",
    "meta": "super",
    "meta_l": "super",
    "meta_r": "super",
    "esc": "esc",
    "escape": "esc",
}


def normalize_key(name: str) -> str:
    """Map a key spelling onto the canonical name used by the state machine."""
    cleaned = name.strip().lower()
    return _KEY_ALIASES.get(cleaned, cleaned)


def parse_chord(keys: str) -> tuple[str, ...]:
    """Turn a config chord such as ``"alt+l"`` into normalised key names."""
    parts = [normalize_key(part) for part in keys.split("+") if part.strip()]
    seen: list[str] = []
    for part in parts:
        if part not in seen:
            seen.append(part)
    return tuple(seen)


class StateMachine:
    """Turns key events into recording actions.

    The chord arms when every key of it is held at the same time (FR-1.2) and
    disarms again only once *all* of them have been released (FR-1.5). A guard
    modifier such as Ctrl blocks the chord outright — it does not merely delay
    it until the modifier is let go.
    """

    def __init__(self, trigger: TriggerConfig) -> None:
        self._trigger = trigger
        self._chord = parse_chord(trigger.keys)
        self._cancel = normalize_key(trigger.cancel_key)
        self._guards: set[str] = set()
        if trigger.ignore_ctrl:
            self._guards.add("ctrl")
        if trigger.ignore_shift:
            self._guards.add("shift")
        if trigger.ignore_super:
            self._guards.add("super")
        self._state = State.IDLE
        self._held: set[str] = set()
        self._consumed = False

    @property
    def state(self) -> State:
        return self._state

    @property
    def chord(self) -> tuple[str, ...]:
        return self._chord

    @property
    def held(self) -> frozenset[str]:
        """Keys currently held down."""
        return frozenset(self._held)

    def _chord_ready(self) -> bool:
        return all(key in self._held for key in self._chord)

    def _chord_released(self) -> bool:
        return not any(key in self._held for key in self._chord)

    def on_key_down(self, key: str, *, repeat: bool = False) -> Action:
        """Handle a key press.

        Auto-repeat is caught by the held-key set: X11 repeats a held key by
        sending another press without an intervening release, so the second
        press is already known and changes nothing (FR-1.4). The ``repeat``
        flag lets a caller that can see repeats explicitly say so, which is
        useful for tests and for backends that report them.
        """
        name = normalize_key(key)
        if repeat or name in self._held:
            return Action.NONE
        self._held.add(name)

        if self._state is State.BUSY:
            # A new trigger while transcribing is ignored, never queued (§7.3).
            return Action.NONE

        if self._state is State.RECORDING:
            if name == self._cancel:
                self._state = State.IDLE
                return Action.CANCEL_RECORDING
            return Action.NONE

        if name == self._cancel:
            return Action.NONE

        if self._consumed or not self._chord_ready():
            return Action.NONE

        if self._guards & self._held:
            # Ctrl/Shift/Super held: refuse this chord and require a full
            # release before it can arm again (FR-1.2).
            self._consumed = True
            return Action.NONE

        self._consumed = True
        self._state = State.RECORDING
        return Action.START_RECORDING

    def on_key_up(self, key: str) -> Action:
        """Handle a key release."""
        name = normalize_key(key)
        self._held.discard(name)

        action = Action.NONE
        if self._state is State.RECORDING and name in self._chord:
            # Either half of the chord going up ends the recording (FR-1.3).
            self._state = State.BUSY
            action = Action.STOP_RECORDING

        if self._chord_released():
            self._consumed = False
        return action

    def abort_recording(self) -> None:
        """Give up on an in-flight recording, e.g. the microphone failed."""
        if self._state is State.RECORDING:
            self._state = State.IDLE

    def auto_stop(self) -> None:
        """The recording was cut short by the duration limit.

        Moves to BUSY like a normal stop would, so releasing the key later does
        not stop a second time and the worker stays the only owner of the
        clipboard.
        """
        if self._state is State.RECORDING:
            self._state = State.BUSY

    def finish_busy(self) -> None:
        """The worker is done; the daemon can accept a new trigger."""
        if self._state is State.BUSY:
            self._state = State.IDLE
