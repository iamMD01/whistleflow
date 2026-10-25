"""Unit tests for the TOML configuration loader.

Every test runs against a temporary file and a scrubbed environment: the
developer's real ``~/.config/whistleflow/config.toml`` and any
``WHISTLEFLOW_*`` variables must never influence a result (FR-6.1, FR-6.3).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from whistleflow.config import (
    AudioConfig,
    FeedbackConfig,
    InjectConfig,
    LoggingConfig,
    SttConfig,
    TriggerConfig,
    config_path,
    load_config,
    lock_path,
    log_path,
    state_dir,
)

#: Every environment variable that can change the loaded configuration.
ENV_VARS = (
    "WHISTLEFLOW_CONFIG",
    "WHISTLEFLOW_LOG_LEVEL",
    "WHISTLEFLOW_LANGUAGE",
    "WHISTLEFLOW_DEVICE",
)


@pytest.fixture(autouse=True)
def _scrubbed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the real environment overrides so defaults are deterministic."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------- defaults


def test_missing_file_yields_documented_defaults(tmp_path: Path) -> None:
    """A missing config file must not be an error: it means "use defaults"."""
    cfg = load_config(tmp_path / "does-not-exist.toml")

    assert cfg.trigger == TriggerConfig(
        keys="alt+l",
        min_duration_ms=300,
        max_duration_s=60,
        cancel_key="esc",
        ignore_ctrl=True,
        ignore_shift=True,
        ignore_super=True,
    )
    assert cfg.audio == AudioConfig(
        device="default",
        debug_save=False,
        debug_dir="~/.local/state/whistleflow/audio",
    )
    assert cfg.stt == SttConfig(language="en", keywords=(), warmup=True)
    assert cfg.inject == InjectConfig(
        method="clipboard",
        paste_key="ctrl+v",
        trailing_space=True,
        restore_clipboard=False,
        terminal_overrides={},
    )
    assert cfg.feedback == FeedbackConfig(mode="beep", volume=20000)
    assert cfg.logging == LoggingConfig(level="INFO", log_transcripts=False)


def test_partial_file_keeps_the_other_defaults(tmp_path: Path) -> None:
    """Only the keys present in the file may change; the rest stay default."""
    path = _write(tmp_path, '[stt]\nlanguage = "de"\n')
    cfg = load_config(path)

    assert cfg.stt.language == "de"
    assert cfg.stt.warmup is True
    assert cfg.trigger == TriggerConfig()
    assert cfg.feedback.volume == 20000


def test_empty_file_yields_defaults(tmp_path: Path) -> None:
    """An empty file is valid TOML and must behave like a missing file."""
    assert load_config(_write(tmp_path, "")) == load_config(tmp_path / "nope.toml")


# ---------------------------------------------------------------- invalid values


def test_unsupported_language_falls_back_to_english(tmp_path: Path) -> None:
    """§9.3: a language outside the allowed set falls back to "en"."""
    cfg = load_config(_write(tmp_path, '[stt]\nlanguage = "klingon"\n'))
    assert cfg.stt.language == "en"


def test_unsupported_language_is_case_insensitive_and_trimmed(tmp_path: Path) -> None:
    """ " DE " is a legitimate spelling of German and must be accepted."""
    cfg = load_config(_write(tmp_path, '[stt]\nlanguage = " DE "\n'))
    assert cfg.stt.language == "de"


def test_auto_language_is_allowed(tmp_path: Path) -> None:
    """ "auto" maps to language detection and is part of the allowed set."""
    assert load_config(_write(tmp_path, '[stt]\nlanguage = "auto"\n')).stt.language == "auto"


def test_invalid_paste_key_falls_back_to_ctrl_v(tmp_path: Path) -> None:
    """§9.3: a paste key that is not a key sequence shape falls back."""
    cfg = load_config(_write(tmp_path, '[inject]\npaste_key = "ctrl+"\n'))
    assert cfg.inject.paste_key == "ctrl+v"


def test_paste_key_with_plus_and_shift_is_kept(tmp_path: Path) -> None:
    """A multi-modifier shortcut is a valid xdotool shape and must survive."""
    cfg = load_config(_write(tmp_path, '[inject]\npaste_key = "ctrl+shift+v"\n'))
    assert cfg.inject.paste_key == "ctrl+shift+v"


def test_unsupported_inject_method_falls_back_to_clipboard(tmp_path: Path) -> None:
    """v1 only supports clipboard injection (FR-4.1)."""
    cfg = load_config(_write(tmp_path, '[inject]\nmethod = "xdotool type"\n'))
    assert cfg.inject.method == "clipboard"


def test_unknown_feedback_mode_falls_back_to_beep(tmp_path: Path) -> None:
    """An unknown mode must not silence the user; the default is "beep"."""
    cfg = load_config(_write(tmp_path, '[feedback]\nmode = "klaxon"\n'))
    assert cfg.feedback.mode == "beep"


def test_unknown_log_level_falls_back_to_info(tmp_path: Path) -> None:
    """An unknown level falls back to the default rather than crashing."""
    cfg = load_config(_write(tmp_path, '[logging]\nlevel = "chatty"\n'))
    assert cfg.logging.level == "INFO"


def test_log_level_is_normalised_to_upper_case(tmp_path: Path) -> None:
    """Log levels are case-insensitive in the file format."""
    assert load_config(_write(tmp_path, '[logging]\nlevel = "debug"\n')).logging.level == "DEBUG"


def test_non_string_value_falls_back_to_default(tmp_path: Path) -> None:
    """A wrongly typed value falls back instead of raising (FR-6.3)."""
    cfg = load_config(_write(tmp_path, "[trigger]\nkeys = 42\n"))
    assert cfg.trigger.keys == "alt+l"


def test_broken_toml_falls_back_to_defaults(tmp_path: Path) -> None:
    """A syntactically broken file must not stop the daemon (FR-6.3)."""
    cfg = load_config(_write(tmp_path, "this is not = = toml\n"))
    assert cfg.trigger == TriggerConfig()


def test_section_that_is_not_a_table_is_ignored(tmp_path: Path) -> None:
    """A scalar where a table is expected is ignored, not fatal."""
    cfg = load_config(_write(tmp_path, "trigger = 5\n"))
    assert cfg.trigger == TriggerConfig()


# ------------------------------------------------------------------ clamping


def test_min_duration_is_clamped_to_the_allowed_range(tmp_path: Path) -> None:
    """§9.3: min_duration_ms is clamped to 50..5000."""
    assert (
        load_config(_write(tmp_path, "[trigger]\nmin_duration_ms = 1\n")).trigger.min_duration_ms
        == 50
    )
    assert (
        load_config(
            _write(tmp_path, "[trigger]\nmin_duration_ms = 999999\n")
        ).trigger.min_duration_ms
        == 5000
    )


def test_max_duration_is_clamped_to_the_allowed_range(tmp_path: Path) -> None:
    """§9.3: max_duration_s is clamped to 1..300."""
    assert (
        load_config(_write(tmp_path, "[trigger]\nmax_duration_s = 0\n")).trigger.max_duration_s == 1
    )
    assert (
        load_config(_write(tmp_path, "[trigger]\nmax_duration_s = 10000\n")).trigger.max_duration_s
        == 300
    )


def test_volume_is_clamped_to_the_allowed_range(tmp_path: Path) -> None:
    """§9.3: the paplay volume is clamped to 0..65536."""
    assert load_config(_write(tmp_path, "[feedback]\nvolume = -10\n")).feedback.volume == 0
    assert load_config(_write(tmp_path, "[feedback]\nvolume = 999999\n")).feedback.volume == 65536


def test_empty_trigger_keys_fall_back_to_the_default_chord(tmp_path: Path) -> None:
    """An empty chord would make the daemon deaf; the default is restored."""
    cfg = load_config(_write(tmp_path, '[trigger]\nkeys = "   "\n'))
    assert cfg.trigger.keys == "alt+l"


def test_empty_cancel_key_falls_back_to_escape(tmp_path: Path) -> None:
    """An empty cancel key would remove the Esc escape hatch."""
    cfg = load_config(_write(tmp_path, '[trigger]\ncancel_key = ""\n'))
    assert cfg.trigger.cancel_key == "esc"


# ------------------------------------------------------------------- coercion


def test_boolean_strings_are_accepted(tmp_path: Path) -> None:
    """TOML users often write "true"/"false" as strings; both are understood."""
    cfg = load_config(
        _write(tmp_path, '[trigger]\nignore_ctrl = "false"\n[logging]\nlog_transcripts = "yes"\n')
    )
    assert cfg.trigger.ignore_ctrl is False
    assert cfg.logging.log_transcripts is True


def test_non_boolean_value_falls_back_to_the_default(tmp_path: Path) -> None:
    """An integer is not a boolean: the default is kept and a warning logged."""
    cfg = load_config(_write(tmp_path, "[trigger]\nignore_ctrl = 0\n"))
    assert cfg.trigger.ignore_ctrl is True


def test_integer_strings_are_accepted(tmp_path: Path) -> None:
    """Numbers written as strings are still usable."""
    assert (
        load_config(
            _write(tmp_path, '[trigger]\nmin_duration_ms = "250"\n')
        ).trigger.min_duration_ms
        == 250
    )


def test_non_integer_value_falls_back_to_the_default(tmp_path: Path) -> None:
    """A non-numeric value falls back instead of raising."""
    cfg = load_config(_write(tmp_path, '[trigger]\nmin_duration_ms = "soon"\n'))
    assert cfg.trigger.min_duration_ms == 300


def test_whole_float_is_accepted(tmp_path: Path) -> None:
    """A float with no fractional part is unambiguous and accepted."""
    cfg = load_config(_write(tmp_path, "[trigger]\nmax_duration_s = 45.0\n"))
    assert cfg.trigger.max_duration_s == 45


# -------------------------------------------------------------------- keywords


def test_keywords_accept_a_bare_string(tmp_path: Path) -> None:
    """A single keyword written as a string is wrapped into a one-item list."""
    cfg = load_config(_write(tmp_path, '[stt]\nkeywords = "whistleflow"\n'))
    assert cfg.stt.keywords == ("whistleflow",)


def test_keywords_drop_empty_and_non_string_entries(tmp_path: Path) -> None:
    """Malformed keywords are skipped; the usable ones are kept (FR-3.5)."""
    cfg = load_config(_write(tmp_path, '[stt]\nkeywords = ["one", "", 5, " two "]\n'))
    assert cfg.stt.keywords == ("one", "two")


def test_keywords_of_the_wrong_type_are_ignored(tmp_path: Path) -> None:
    """A number where a list is expected must not crash the loader."""
    cfg = load_config(_write(tmp_path, "[stt]\nkeywords = 7\n"))
    assert cfg.stt.keywords == ()


# ------------------------------------------------------------- terminal overrides


def test_terminal_overrides_are_lowercased(tmp_path: Path) -> None:
    """§9.2: the window class is lowercased so lookups are case-insensitive."""
    cfg = load_config(
        _write(tmp_path, '[inject]\nterminal_overrides = { Konsole = "CTRL+SHIFT+V" }\n')
    )
    assert cfg.inject.terminal_overrides == {"konsole": "ctrl+shift+v"}


def test_invalid_terminal_override_is_dropped(tmp_path: Path) -> None:
    """A malformed override is skipped, the valid neighbours survive."""
    cfg = load_config(
        _write(
            tmp_path, '[inject]\nterminal_overrides = { konsole = "ctrl+shift+v", kitty = "!!" }\n'
        )
    )
    assert cfg.inject.terminal_overrides == {"konsole": "ctrl+shift+v"}


def test_terminal_overrides_of_the_wrong_type_are_ignored(tmp_path: Path) -> None:
    """A non-table value must not crash the loader."""
    cfg = load_config(_write(tmp_path, '[inject]\nterminal_overrides = "konsole"\n'))
    assert cfg.inject.terminal_overrides == {}


# ----------------------------------------------------------------- unknown keys


def test_unknown_keys_are_ignored_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """§9.3: unknown keys log a warning and are ignored, not fatal."""
    path = _write(tmp_path, '[trigger]\nkeys = "alt+l"\nmystery = 1\n[feedback]\ncolour = "red"\n')
    with caplog.at_level(logging.WARNING, logger="whistleflow.config"):
        cfg = load_config(path)

    assert cfg.trigger.keys == "alt+l"
    assert cfg.feedback.mode == "beep"
    assert "trigger.mystery" in caplog.text
    assert "feedback.colour" in caplog.text


# ------------------------------------------------------------------ path handling


def test_config_path_follows_xdg_config_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """§9.1: the config lives under $XDG_CONFIG_HOME when that is set."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert config_path() == tmp_path / "xdg" / "whistleflow" / "config.toml"


def test_config_path_falls_back_to_dot_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without XDG_CONFIG_HOME the historical ~/.config path is used."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config_path() == tmp_path / ".config" / "whistleflow" / "config.toml"


def test_state_and_log_paths_follow_xdg_state_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FR-7.6: the rotating log lives under $XDG_STATE_HOME/whistleflow."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert state_dir() == tmp_path / "state" / "whistleflow"
    assert log_path() == tmp_path / "state" / "whistleflow" / "whistleflow.log"


def test_tilde_in_the_config_path_is_expanded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A "~" in the config path must be expanded, otherwise the file is missed."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('[logging]\nlevel = "DEBUG"\n', encoding="utf-8")

    cfg = load_config(Path("~/config.toml"))
    assert cfg.logging.level == "DEBUG"


def test_the_lock_file_lives_in_the_runtime_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FR-7.2: the single-instance lock is taken from $XDG_RUNTIME_DIR."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert lock_path() == tmp_path / "whistleflow.lock"


def test_the_lock_file_falls_back_to_a_per_user_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-7.2: without XDG_RUNTIME_DIR the fallback carries the uid."""
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    expected = Path(f"/tmp/whistleflow-{os.getuid()}.lock")  # noqa: S108 - mirrors the daemon
    assert lock_path() == expected


def test_tilde_in_the_config_environment_variable_is_expanded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FR-6.5: WHISTLEFLOW_CONFIG may use "~" as well."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('[logging]\nlevel = "DEBUG"\n', encoding="utf-8")
    monkeypatch.setenv("WHISTLEFLOW_CONFIG", "~/config.toml")

    assert load_config().logging.level == "DEBUG"


def test_config_environment_variable_selects_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FR-6.5: WHISTLEFLOW_CONFIG points at an alternative config file."""
    path = _write(tmp_path, '[stt]\nlanguage = "fr"\n')
    monkeypatch.setenv("WHISTLEFLOW_CONFIG", str(path))
    assert load_config().stt.language == "fr"


# ------------------------------------------------------------- environment overrides


def test_environment_overrides_beat_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FR-6.5 / §9.4: environment overrides win over the config file."""
    path = _write(
        tmp_path,
        '[logging]\nlevel = "WARNING"\n[stt]\nlanguage = "de"\n[audio]\ndevice = "bluez"\n',
    )
    monkeypatch.setenv("WHISTLEFLOW_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("WHISTLEFLOW_LANGUAGE", "fr")
    monkeypatch.setenv("WHISTLEFLOW_DEVICE", "pulse")

    cfg = load_config(path)
    assert cfg.logging.level == "DEBUG"
    assert cfg.stt.language == "fr"
    assert cfg.audio.device == "pulse"


def test_invalid_environment_overrides_are_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A bad environment value must not override a good file value."""
    path = _write(tmp_path, '[logging]\nlevel = "WARNING"\n[stt]\nlanguage = "de"\n')
    monkeypatch.setenv("WHISTLEFLOW_LOG_LEVEL", "LOUD")
    monkeypatch.setenv("WHISTLEFLOW_LANGUAGE", "klingon")

    cfg = load_config(path)
    assert cfg.logging.level == "WARNING"
    assert cfg.stt.language == "de"


def test_empty_device_environment_variable_keeps_the_file_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An empty WHISTLEFLOW_DEVICE must not blank out a configured device."""
    path = _write(tmp_path, '[audio]\ndevice = "bluez"\n')
    monkeypatch.setenv("WHISTLEFLOW_DEVICE", "   ")

    assert load_config(path).audio.device == "bluez"
