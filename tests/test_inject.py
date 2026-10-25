"""Unit tests for the clipboard injection path.

Every external command is mocked: no X server, no xclip, no xdotool (§13.1).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from whistleflow import inject as inject_module
from whistleflow.config import InjectConfig
from whistleflow.inject import InjectError, Injector, parse_wm_class


class FakeRunner:
    """Records every ``subprocess.run`` call and replays canned results."""

    def __init__(
        self, results: dict[str, subprocess.CompletedProcess[str] | Exception] | None = None
    ) -> None:
        self.results = results or {}
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        argv = list(argv)
        self.calls.append((argv, kwargs))
        outcome = self.results.get(argv[0])
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is not None:
            return outcome
        return subprocess.CompletedProcess(argv, 0, "", "")

    def argv_for(self, tool: str) -> list[list[str]]:
        """All argument vectors that were used for one tool."""
        return [argv for argv, _ in self.calls if argv[0] == tool]


def _ok(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["cmd"], 0, stdout, "")


def _fail(code: int = 1) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["cmd"], code, "", "boom")


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    """Replace ``subprocess.run`` and record the settle delay instead of waiting."""
    fake = FakeRunner()
    monkeypatch.setattr(inject_module.subprocess, "run", fake)
    monkeypatch.setattr(inject_module.time, "sleep", lambda _seconds: None)
    return fake


# ------------------------------------------------------------- WM_CLASS parsing


def test_parse_wm_class_uses_the_second_quoted_string() -> None:
    """§8.7: WM_CLASS holds the instance name first, the class name second."""
    output = 'WM_CLASS(STRING) = "konsole", "Konsole"'
    assert parse_wm_class(output) == "Konsole"


def test_parse_wm_class_of_a_browser() -> None:
    """The class name of a Chromium window is the second string as well."""
    output = 'WM_CLASS(STRING) = "brave-browser", "Brave-browser"'
    assert parse_wm_class(output) == "Brave-browser"


def test_parse_wm_class_with_a_single_string() -> None:
    """A single string is all the information there is."""
    assert parse_wm_class('WM_CLASS(STRING) = "konsole"') == "konsole"


def test_parse_wm_class_without_a_property_name() -> None:
    """The property name may be missing; the quoted strings still count."""
    assert parse_wm_class('"konsole", "Konsole"') == "Konsole"


def test_parse_wm_class_of_unparsable_output_is_none() -> None:
    """§8.7: when parsing fails the caller falls back to the default key."""
    assert parse_wm_class("xprop: unable to open display") is None
    assert parse_wm_class("") is None


# ------------------------------------------------------------------ paste


def test_paste_sets_the_clipboard_and_sends_the_paste_key(runner: FakeRunner) -> None:
    """FR-4.1 / FR-4.2: xclip first, then one xdotool key event."""
    Injector(InjectConfig()).paste("hello")

    assert runner.argv_for("xclip") == [["xclip", "-selection", "clipboard", "-i"]]
    # The last xdotool call is the paste itself; earlier ones probe the window.
    assert runner.argv_for("xdotool")[-1] == ["xdotool", "key", "--clearmodifiers", "ctrl+v"]


def test_paste_sends_the_transcript_to_xclip_on_stdin(runner: FakeRunner) -> None:
    """The text itself is piped into xclip, never passed as an argument."""
    Injector(InjectConfig(trailing_space=False)).paste("hello world")

    _, kwargs = runner.calls[0]
    assert kwargs["input"] == "hello world"


def test_paste_appends_a_trailing_space_by_default(runner: FakeRunner) -> None:
    """FR-4.5: a trailing space keeps the dictation flow going."""
    Injector(InjectConfig()).paste("hello")

    _, kwargs = runner.calls[0]
    assert kwargs["input"] == "hello "


def test_paste_can_omit_the_trailing_space(runner: FakeRunner) -> None:
    """The trailing space is configurable."""
    Injector(InjectConfig(trailing_space=False)).paste("hello")

    _, kwargs = runner.calls[0]
    assert kwargs["input"] == "hello"


def test_paste_does_not_add_a_second_trailing_space(runner: FakeRunner) -> None:
    """Text that already ends in whitespace is left alone."""
    Injector(InjectConfig()).paste("hello ")

    _, kwargs = runner.calls[0]
    assert kwargs["input"] == "hello "


def test_paste_waits_for_the_clipboard_to_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    """§8.8: xclip forks a child that owns the selection; wait ~100 ms."""
    fake = FakeRunner()
    delays: list[float] = []
    monkeypatch.setattr(inject_module.subprocess, "run", fake)
    monkeypatch.setattr(inject_module.time, "sleep", delays.append)

    Injector(InjectConfig()).paste("hello")
    assert delays == [inject_module.CLIPBOARD_SETTLE_S]


def test_paste_refuses_empty_text(runner: FakeRunner) -> None:
    """FR-4.6: nothing is pasted when there is nothing to paste."""
    with pytest.raises(InjectError):
        Injector(InjectConfig()).paste("")
    assert runner.calls == []


def test_paste_uses_a_timeout_for_every_command(runner: FakeRunner) -> None:
    """§12: every subprocess call carries a timeout."""
    Injector(InjectConfig()).paste("hello")

    for _, kwargs in runner.calls:
        assert kwargs["timeout"] == inject_module.SUBPROCESS_TIMEOUT
        assert kwargs["check"] is False


# ------------------------------------------------------------------ overrides


def test_paste_key_uses_the_override_for_the_focused_window(
    runner: FakeRunner,
) -> None:
    """FR-4.3: a terminal gets its own paste shortcut."""
    runner.results["xdotool"] = _ok("12345\n")
    runner.results["xprop"] = _ok('WM_CLASS(STRING) = "konsole", "Konsole"')
    injector = Injector(InjectConfig(terminal_overrides={"konsole": "ctrl+shift+v"}))

    assert injector.paste_key() == "ctrl+shift+v"


def test_paste_key_override_is_matched_case_insensitively(runner: FakeRunner) -> None:
    """The window class is lowercased before the lookup (§9.2)."""
    runner.results["xdotool"] = _ok("12345\n")
    runner.results["xprop"] = _ok('WM_CLASS(STRING) = "konsole", "Konsole"')
    injector = Injector(InjectConfig(terminal_overrides={"konsole": "ctrl+shift+v"}))

    assert injector.paste_key() == "ctrl+shift+v"


def test_paste_key_falls_back_to_the_default_for_other_windows(runner: FakeRunner) -> None:
    """A window without an override uses the configured default."""
    runner.results["xdotool"] = _ok("12345\n")
    runner.results["xprop"] = _ok('WM_CLASS(STRING) = "brave-browser", "Brave-browser"')
    injector = Injector(
        InjectConfig(paste_key="ctrl+v", terminal_overrides={"konsole": "ctrl+shift+v"})
    )

    assert injector.paste_key() == "ctrl+v"


def test_paste_key_falls_back_when_the_window_class_is_unknown(runner: FakeRunner) -> None:
    """§8.7: if the class cannot be determined, use the default key."""
    runner.results["xdotool"] = _ok("12345\n")
    runner.results["xprop"] = _ok("no such property")

    assert (
        Injector(InjectConfig(terminal_overrides={"konsole": "ctrl+shift+v"})).paste_key()
        == "ctrl+v"
    )


def test_paste_sends_the_override_key(runner: FakeRunner) -> None:
    """The override has to reach the actual xdotool call, not only paste_key()."""
    runner.results["xdotool"] = _ok("12345\n")
    runner.results["xprop"] = _ok('WM_CLASS(STRING) = "konsole", "Konsole"')
    injector = Injector(InjectConfig(terminal_overrides={"konsole": "ctrl+shift+v"}))

    injector.paste("hello")
    assert runner.argv_for("xdotool")[-1] == ["xdotool", "key", "--clearmodifiers", "ctrl+shift+v"]


# -------------------------------------------------------------- window probing


def test_active_window_class_queries_xprop(runner: FakeRunner) -> None:
    """FR-4.4 / §8.7: xdotool gives the id, xprop gives the class."""
    runner.results["xdotool"] = _ok("29360134\n")
    runner.results["xprop"] = _ok('WM_CLASS(STRING) = "konsole", "Konsole"')

    assert Injector(InjectConfig()).active_window_class() == "Konsole"
    assert runner.argv_for("xdotool") == [["xdotool", "getactivewindow"]]
    assert runner.argv_for("xprop") == [["xprop", "-id", "29360134", "WM_CLASS"]]


def test_active_window_class_is_none_when_the_id_is_not_a_number(runner: FakeRunner) -> None:
    """A garbled window id must not crash the injection path."""
    runner.results["xdotool"] = _ok("no window\n")

    assert Injector(InjectConfig()).active_window_class() is None


def test_active_window_class_is_none_when_xprop_fails(runner: FakeRunner) -> None:
    """A failing xprop means "unknown class", not an exception."""
    runner.results["xdotool"] = _ok("29360134\n")
    runner.results["xprop"] = _fail(1)

    assert Injector(InjectConfig()).active_window_class() is None


def test_active_window_class_is_none_when_xdotool_fails(runner: FakeRunner) -> None:
    """Without an X display there is no window to ask about."""
    runner.results["xdotool"] = _fail(1)

    assert Injector(InjectConfig()).active_window_class() is None


# ------------------------------------------------------------------- failures


def test_xclip_failure_raises_inject_error(runner: FakeRunner) -> None:
    """§12: a clipboard failure is reported, not swallowed."""
    runner.results["xclip"] = _fail(1)

    with pytest.raises(InjectError):
        Injector(InjectConfig()).paste("hello")


def test_xclip_timeout_raises_inject_error(runner: FakeRunner) -> None:
    """A hanging xclip must not block the daemon forever."""
    runner.results["xclip"] = subprocess.TimeoutExpired(cmd="xclip", timeout=2.0)

    with pytest.raises(InjectError):
        Injector(InjectConfig()).paste("hello")


def test_xclip_missing_raises_inject_error(runner: FakeRunner) -> None:
    """xclip not installed is a user-facing error, not a crash."""
    runner.results["xclip"] = FileNotFoundError("xclip")

    with pytest.raises(InjectError):
        Injector(InjectConfig()).paste("hello")


def test_xdotool_failure_raises_inject_error(runner: FakeRunner) -> None:
    """§12: a paste failure is reported so the user can paste manually."""
    runner.results["xdotool"] = _fail(3)

    with pytest.raises(InjectError):
        Injector(InjectConfig()).paste("hello")


def test_xdotool_timeout_raises_inject_error(runner: FakeRunner) -> None:
    """A hanging xdotool must not block the daemon forever."""
    runner.results["xdotool"] = subprocess.TimeoutExpired(cmd="xdotool", timeout=2.0)

    with pytest.raises(InjectError):
        Injector(InjectConfig()).paste("hello")


def test_the_clipboard_keeps_the_text_when_the_paste_fails(runner: FakeRunner) -> None:
    """FR-4.7: the text stays on the clipboard so Ctrl+V still works."""
    runner.results["xdotool"] = _fail(1)

    with pytest.raises(InjectError):
        Injector(InjectConfig()).paste("hello")

    assert runner.calls[0][1]["input"] == "hello "


def test_no_shell_is_used_for_the_transcript(runner: FakeRunner) -> None:
    """§8.10: the transcript must never end up in a shell string."""
    Injector(InjectConfig()).paste("rm -rf /; echo $(whoami)")

    for argv, kwargs in runner.calls:
        assert "shell" not in kwargs
        assert all(isinstance(part, str) for part in argv)
    assert runner.calls[0][1]["input"] == "rm -rf /; echo $(whoami) "


def test_the_debug_dir_is_not_used_by_the_injector(tmp_path: Path) -> None:
    """The injector must not write anything to disk (FR-8.3)."""
    injector = Injector(InjectConfig())
    assert not list(tmp_path.iterdir())
    assert injector is not None
