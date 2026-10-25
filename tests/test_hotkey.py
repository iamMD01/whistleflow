"""Unit tests for the hotkey layer.

pynput is imported for its key classes only — no listener is started and no X
server is contacted (§13.1).
"""

from __future__ import annotations

import pytest
from pynput.keyboard import Key, KeyCode

from whistleflow.config import TriggerConfig
from whistleflow.hotkey import Event, HotkeyListener, normalize_pynput_key
from whistleflow.state import Action, State, StateMachine


class NamedKeyCode(KeyCode):
    """A key code that also carries a key name, like some backends do."""

    name = "space"


# ----------------------------------------------------------------- key classes


def test_left_alt_is_classified_as_alt() -> None:
    """The left Alt key must arm the chord; pynput spells it "alt" or "alt_l"."""
    assert normalize_pynput_key(Key.alt_l) == "alt"
    assert normalize_pynput_key(Key.alt) == "alt"


def test_right_alt_is_classified_as_alt() -> None:
    """The right Alt key is a different key code but the same chord member."""
    assert normalize_pynput_key(Key.alt_r) == "alt"


def test_right_alt_is_not_the_same_object_as_left_alt() -> None:
    """Guard against pynput aliasing both Alt keys into one enum member."""
    assert Key.alt_l is not Key.alt_r


def test_a_typed_character_is_classified_by_its_char() -> None:
    """The letter L of the chord arrives as a KeyCode with a character."""
    assert normalize_pynput_key(KeyCode(char="l")) == "l"
    assert normalize_pynput_key(KeyCode(char="L")) == "l"


def test_other_characters_are_classified_by_their_char() -> None:
    """Any character key is reduced to its lowercased character."""
    assert normalize_pynput_key(KeyCode(char="a")) == "a"
    assert normalize_pynput_key(KeyCode(char="é")) == "é"


def test_special_keys_are_classified_by_their_name() -> None:
    """Named keys (Escape, Space, F-keys) keep their canonical name."""
    assert normalize_pynput_key(Key.esc) == "esc"
    assert normalize_pynput_key(Key.space) == "space"
    assert normalize_pynput_key(Key.f1) == "f1"


def test_the_name_of_a_key_code_is_used_when_there_is_no_char() -> None:
    """A key code without a character falls back to its name."""
    assert normalize_pynput_key(NamedKeyCode(vk=32)) == "space"


def test_an_unknown_object_is_ignored() -> None:
    """Anything that is neither a Key nor a KeyCode cannot be classified."""
    assert normalize_pynput_key(None) is None
    assert normalize_pynput_key("l") is None
    assert normalize_pynput_key(42) is None


def test_a_key_code_without_a_character_is_ignored() -> None:
    """A key press that carries no character is not part of any chord.

    pynput's ``KeyCode`` has no ``name`` attribute at all, so keys that map to
    no character (XF86 media keys, some function keys) must be ignored rather
    than raise. Raising here used to kill the listener thread, which silently
    stopped the daemon from reacting to the hotkey (FR-1.7, NFR-10).
    """
    assert normalize_pynput_key(KeyCode(vk=76)) is None


# -------------------------------------------------------------------- listener


class RecordingListener:
    """Collects the events the listener emits."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def __call__(self, event: Event) -> None:
        self.events.append(event)


@pytest.fixture
def listener_and_state() -> tuple[HotkeyListener, StateMachine, RecordingListener]:
    """A listener wired to a real state machine, with a recording callback."""
    machine = StateMachine(TriggerConfig())
    sink = RecordingListener()
    listener = HotkeyListener("alt+l", machine, sink)
    return listener, machine, sink


def test_the_listener_starts_the_recording_on_the_chord(
    listener_and_state: tuple[HotkeyListener, StateMachine, RecordingListener],
) -> None:
    """FR-1.2: pressing Alt and L emits the start action."""
    listener, machine, sink = listener_and_state

    listener._on_press(Key.alt_l)
    listener._on_press(KeyCode(char="l"))

    assert [event.action for event in sink.events] == [Action.START_RECORDING]
    assert machine.state is State.RECORDING


def test_the_listener_stops_the_recording_on_release(
    listener_and_state: tuple[HotkeyListener, StateMachine, RecordingListener],
) -> None:
    """FR-1.3: releasing one of the chord keys emits the stop action."""
    listener, machine, sink = listener_and_state

    listener._on_press(Key.alt_l)
    listener._on_press(KeyCode(char="l"))
    listener._on_release(KeyCode(char="l"))

    assert [event.action for event in sink.events] == [
        Action.START_RECORDING,
        Action.STOP_RECORDING,
    ]
    assert machine.state is State.BUSY


def test_the_listener_ignores_unclassifiable_keys(
    listener_and_state: tuple[HotkeyListener, StateMachine, RecordingListener],
) -> None:
    """A key the daemon cannot classify must not produce any event."""
    listener, _machine, sink = listener_and_state

    listener._on_press(None)
    listener._on_release("l")

    assert sink.events == []


def test_the_listener_survives_a_keycode_without_a_character(
    listener_and_state: tuple[HotkeyListener, StateMachine, RecordingListener],
) -> None:
    """A media key press must not kill the listener thread.

    pynput reports keys that map to no character as a ``KeyCode`` with
    ``char is None``. Touching the ``.name`` attribute of such a key used to
    raise inside the pynput callback, which killed the listener and left the
    daemon deaf to the hotkey while still looking alive (FR-1.7, NFR-10).
    """
    listener, machine, sink = listener_and_state

    listener._on_press(KeyCode(vk=76))
    listener._on_release(KeyCode(vk=76))

    assert sink.events == []
    assert machine.state is State.IDLE

    # The listener still works afterwards.
    listener._on_press(Key.alt_l)
    listener._on_press(KeyCode(char="l"))

    assert [event.action for event in sink.events] == [Action.START_RECORDING]


def test_the_listener_survives_a_failing_state_machine() -> None:
    """NFR-10: a bug in the callback must never kill the listener thread."""

    class ExplodingMachine:
        def on_key_down(self, _key: str) -> Action:
            raise RuntimeError("boom")

        def on_key_up(self, _key: str) -> Action:
            raise RuntimeError("boom")

    sink = RecordingListener()
    listener = HotkeyListener("alt+l", ExplodingMachine(), sink)  # type: ignore[arg-type]

    listener._on_press(Key.alt_l)
    listener._on_release(Key.alt_l)

    assert sink.events == []


def test_the_listener_is_not_running_before_it_is_started(
    listener_and_state: tuple[HotkeyListener, StateMachine, RecordingListener],
) -> None:
    """Starting a listener needs an X display, which the tests do not have."""
    listener, _machine, _sink = listener_and_state
    assert listener.running is False


def test_stopping_a_listener_that_never_started_is_harmless(
    listener_and_state: tuple[HotkeyListener, StateMachine, RecordingListener],
) -> None:
    """Shutdown must not raise when the listener never came up."""
    listener, _machine, _sink = listener_and_state
    listener.stop()
    assert listener.running is False
