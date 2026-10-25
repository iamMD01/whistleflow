"""Unit tests for the trigger state machine.

The state machine is pure logic: no threads, no keyboard, no X server. Every
transition required by §7.3 / FR-1 is exercised here directly (§13.1).
"""

from __future__ import annotations

import pytest

from whistleflow.config import TriggerConfig
from whistleflow.state import (
    Action,
    State,
    StateMachine,
    normalize_key,
    parse_chord,
)


@pytest.fixture
def machine() -> StateMachine:
    """A default trigger: Alt+L, Esc cancels, Ctrl/Shift/Super guard."""
    return StateMachine(TriggerConfig())


# --------------------------------------------------------------------- helpers


def test_normalize_key_maps_the_documented_aliases() -> None:
    """The same key is spelled differently by pynput, xdotool and users."""
    assert normalize_key("Alt_L") == "alt"
    assert normalize_key("alt_r") == "alt"
    assert normalize_key("option") == "alt"
    assert normalize_key("ctrl") == "ctrl"
    assert normalize_key("control") == "ctrl"
    assert normalize_key("cmd_l") == "super"
    assert normalize_key("meta_r") == "super"
    assert normalize_key("escape") == "esc"
    assert normalize_key("  ESC  ") == "esc"


def test_normalize_key_leaves_unknown_names_alone() -> None:
    """An unknown key keeps its own (lowercased) name."""
    assert normalize_key("F5") == "f5"
    assert normalize_key("space") == "space"


def test_parse_chord_normalises_and_deduplicates() -> None:
    """A chord is a set of canonical key names, order and duplicates aside."""
    assert parse_chord("alt+l") == ("alt", "l")
    assert parse_chord("Alt+L") == ("alt", "l")
    assert parse_chord("alt+alt+l") == ("alt", "l")
    assert parse_chord("alt + l") == ("alt", "l")


# ------------------------------------------------------------- idle transitions


def test_single_key_does_not_start_recording(machine: StateMachine) -> None:
    """FR-1.2: both keys of the chord must be down before recording starts."""
    assert machine.on_key_down("alt") is Action.NONE
    assert machine.state is State.IDLE


def test_chord_arms_recording(machine: StateMachine) -> None:
    """FR-1.2: Alt+L together starts the recording."""
    assert machine.on_key_down("alt") is Action.NONE
    assert machine.on_key_down("l") is Action.START_RECORDING
    assert machine.state is State.RECORDING


def test_chord_is_reported_in_canonical_form(machine: StateMachine) -> None:
    """The chord is normalised once, at construction time."""
    assert machine.chord == ("alt", "l")


# --------------------------------------------------------------- key auto-repeat


def test_key_repeat_does_not_start_a_second_recording(machine: StateMachine) -> None:
    """FR-1.4: X11 auto-repeat must not start additional recordings."""
    machine.on_key_down("alt")
    machine.on_key_down("l")
    assert machine.on_key_down("l", repeat=True) is Action.NONE
    assert machine.on_key_down("alt", repeat=True) is Action.NONE
    assert machine.state is State.RECORDING


def test_repeated_press_without_a_release_is_ignored(machine: StateMachine) -> None:
    """A second press event for a key that is still down is a repeat too."""
    machine.on_key_down("alt")
    assert machine.on_key_down("alt") is Action.NONE
    assert machine.state is State.IDLE


def test_repeat_of_a_single_key_never_arms_the_chord(machine: StateMachine) -> None:
    """Auto-repeat of Alt alone must not complete the chord."""
    assert machine.on_key_down("alt", repeat=True) is Action.NONE
    assert machine.state is State.IDLE


# ------------------------------------------------------------------ release order


def test_releasing_l_first_stops_the_recording(machine: StateMachine) -> None:
    """FR-1.3: releasing either half of the chord ends the recording."""
    machine.on_key_down("alt")
    machine.on_key_down("l")
    assert machine.on_key_up("l") is Action.STOP_RECORDING
    assert machine.state is State.BUSY


def test_releasing_alt_first_stops_the_recording(machine: StateMachine) -> None:
    """The other release order must behave identically."""
    machine.on_key_down("alt")
    machine.on_key_down("l")
    assert machine.on_key_up("alt") is Action.STOP_RECORDING
    assert machine.state is State.BUSY


def test_releasing_an_unrelated_key_does_not_stop_the_recording(machine: StateMachine) -> None:
    """Only the chord keys end a recording; other keys must not."""
    machine.on_key_down("alt")
    machine.on_key_down("l")
    assert machine.on_key_up("x") is Action.NONE
    assert machine.state is State.RECORDING


# --------------------------------------------------------------- guard modifiers


@pytest.mark.parametrize("modifier", ["ctrl", "shift", "super"])
def test_guard_modifier_blocks_the_chord(modifier: str) -> None:
    """FR-1.2: Ctrl/Shift/Super held down must block the trigger entirely."""
    machine = StateMachine(TriggerConfig())
    machine.on_key_down(modifier)
    machine.on_key_down("alt")

    assert machine.on_key_down("l") is Action.NONE
    assert machine.state is State.IDLE


def test_guard_modifier_pressed_after_the_chord_still_blocks_it() -> None:
    """The guard is checked when the chord becomes ready, whatever the order."""
    machine = StateMachine(TriggerConfig())
    machine.on_key_down("alt")
    machine.on_key_down("ctrl")
    assert machine.on_key_down("l") is Action.NONE
    assert machine.state is State.IDLE


def test_guard_modifier_can_be_configured_away() -> None:
    """ignore_ctrl = false means Ctrl+Alt+L is an accepted trigger."""
    machine = StateMachine(TriggerConfig(ignore_ctrl=False))
    machine.on_key_down("ctrl")
    machine.on_key_down("alt")
    assert machine.on_key_down("l") is Action.START_RECORDING
    assert machine.state is State.RECORDING


def test_releasing_the_guard_does_not_arm_a_blocked_chord(machine: StateMachine) -> None:
    """A blocked chord needs a full release before it may arm again."""
    machine.on_key_down("ctrl")
    machine.on_key_down("alt")
    machine.on_key_down("l")
    machine.on_key_up("ctrl")

    assert machine.on_key_down("l") is Action.NONE
    assert machine.state is State.IDLE


# -------------------------------------------------------------------- Esc cancel


def test_escape_cancels_the_recording(machine: StateMachine) -> None:
    """FR-1.6: Esc during recording aborts and discards."""
    machine.on_key_down("alt")
    machine.on_key_down("l")
    assert machine.on_key_down("esc") is Action.CANCEL_RECORDING
    assert machine.state is State.IDLE


def test_escape_in_idle_is_ignored(machine: StateMachine) -> None:
    """Esc outside a recording must not start anything."""
    assert machine.on_key_down("esc") is Action.NONE
    assert machine.state is State.IDLE


def test_escape_key_is_configurable() -> None:
    """cancel_key is configurable and normalised through the same alias table."""
    machine = StateMachine(TriggerConfig(cancel_key="escape"))
    machine.on_key_down("alt")
    machine.on_key_down("l")
    assert machine.on_key_down("esc") is Action.CANCEL_RECORDING
    assert machine.state is State.IDLE


# ------------------------------------------------------------------ re-arming


def test_chord_must_be_fully_released_before_it_arms_again(machine: StateMachine) -> None:
    """FR-1.5: after an utterance the chord has to be released completely."""
    machine.on_key_down("alt")
    machine.on_key_down("l")
    machine.on_key_up("l")
    assert machine.state is State.BUSY

    # Alt is still held: pressing "l" again must not start a new recording.
    assert machine.on_key_down("l") is Action.NONE
    assert machine.state is State.BUSY
    machine.on_key_up("l")

    machine.on_key_up("alt")
    machine.finish_busy()
    assert machine.state is State.IDLE

    assert machine.on_key_down("alt") is Action.NONE
    assert machine.on_key_down("l") is Action.START_RECORDING


def test_finish_busy_returns_to_idle(machine: StateMachine) -> None:
    """BUSY ends only when the worker says it is done (§7.3)."""
    machine.on_key_down("alt")
    machine.on_key_down("l")
    machine.on_key_up("alt")
    assert machine.state is State.BUSY

    machine.finish_busy()
    assert machine.state is State.IDLE


def test_abort_recording_returns_to_idle(machine: StateMachine) -> None:
    """A failed recording aborts back to IDLE (§7.3)."""
    machine.on_key_down("alt")
    machine.on_key_down("l")
    machine.abort_recording()
    assert machine.state is State.IDLE


def test_abort_recording_does_not_disturb_the_busy_state(machine: StateMachine) -> None:
    """abort_recording only applies to a recording in progress."""
    machine.on_key_down("alt")
    machine.on_key_down("l")
    machine.on_key_up("alt")
    machine.abort_recording()
    assert machine.state is State.BUSY


# ------------------------------------------------------------------------ busy


def test_busy_ignores_a_new_trigger(machine: StateMachine) -> None:
    """FR-7.3 / §4.2: a trigger while transcribing is ignored, never queued."""
    machine.on_key_down("alt")
    machine.on_key_down("l")
    machine.on_key_up("alt")
    assert machine.state is State.BUSY

    assert machine.on_key_down("alt") is Action.NONE
    assert machine.on_key_down("l") is Action.NONE
    assert machine.state is State.BUSY


def test_busy_ignores_the_escape_key(machine: StateMachine) -> None:
    """Esc must not cancel a job that is already being transcribed."""
    machine.on_key_down("alt")
    machine.on_key_down("l")
    machine.on_key_up("alt")

    assert machine.on_key_down("esc") is Action.NONE
    assert machine.state is State.BUSY


def test_busy_survives_repeated_triggers_until_the_worker_finishes(machine: StateMachine) -> None:
    """The busy state survives several trigger attempts in a row."""
    machine.on_key_down("alt")
    machine.on_key_down("l")
    machine.on_key_up("l")

    for _ in range(3):
        machine.on_key_down("alt")
        machine.on_key_down("l")
        machine.on_key_up("l")
        machine.on_key_up("alt")
        assert machine.state is State.BUSY

    machine.finish_busy()
    assert machine.state is State.IDLE


def test_held_keys_are_reported(machine: StateMachine) -> None:
    """The held set is what the hotkey layer and the app reason about."""
    machine.on_key_down("alt")
    assert machine.held == frozenset({"alt"})
    machine.on_key_down("l")
    assert machine.held == frozenset({"alt", "l"})
    machine.on_key_up("l")
    assert machine.held == frozenset({"alt"})
