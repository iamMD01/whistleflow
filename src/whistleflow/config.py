"""Configuration dataclasses and TOML loading.

Config lives at ``$XDG_CONFIG_HOME/whistleflow/config.toml``. Every key has a
built-in default and every invalid value falls back to that default with a
warning, so a broken config file never stops the daemon (FR-6.2, FR-6.3).
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

LANGUAGES = frozenset({"en", "auto", "de", "fr", "es", "it", "nl", "pl"})
FEEDBACK_MODES = frozenset({"beep", "notify", "none"})
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

# "ctrl+v", "ctrl+shift+v", "super" — the shape xdotool accepts.
PASTE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9]+(?:\+[A-Za-z0-9]+)*$")

MIN_DURATION_MS_RANGE = (50, 5000)
MAX_DURATION_S_RANGE = (1, 300)
VOLUME_RANGE = (0, 65536)


def _xdg_dir(var: str, fallback: str) -> Path:
    value = os.environ.get(var)
    return Path(value).expanduser() if value else Path(fallback).expanduser()


def config_path() -> Path:
    """Default location of the TOML config file."""
    return _xdg_dir("XDG_CONFIG_HOME", "~/.config") / "whistleflow" / "config.toml"


def state_dir() -> Path:
    """Directory holding the log file and any debug audio."""
    return _xdg_dir("XDG_STATE_HOME", "~/.local/state") / "whistleflow"


def log_path() -> Path:
    """Rotating log file path."""
    return state_dir() / "whistleflow.log"


def lock_path() -> Path:
    """Single-instance lock file, private to this user (FR-7.2)."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "whistleflow.lock"
    # Fallback for a session without XDG_RUNTIME_DIR. The name carries the uid
    # and the file is created with mode 0600, so it cannot be used by others.
    return Path(f"/tmp/whistleflow-{os.getuid()}.lock")  # noqa: S108


@dataclass(frozen=True)
class TriggerConfig:
    """Hotkey and capture window settings."""

    keys: str = "alt+l"
    min_duration_ms: int = 300
    max_duration_s: int = 60
    cancel_key: str = "esc"
    ignore_ctrl: bool = True
    ignore_shift: bool = True
    ignore_super: bool = True


@dataclass(frozen=True)
class AudioConfig:
    """Microphone selection and debug capture."""

    device: str = "default"
    debug_save: bool = False
    debug_dir: str = "~/.local/state/whistleflow/audio"


@dataclass(frozen=True)
class SttConfig:
    """Speech-to-text settings."""

    language: str = "en"
    keywords: tuple[str, ...] = ()
    warmup: bool = True


@dataclass(frozen=True)
class InjectConfig:
    """How the transcript reaches the focused window."""

    method: str = "clipboard"
    paste_key: str = "ctrl+v"
    trailing_space: bool = True
    restore_clipboard: bool = False
    terminal_overrides: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FeedbackConfig:
    """Audible and visual feedback."""

    mode: str = "beep"
    volume: int = 20000


@dataclass(frozen=True)
class LoggingConfig:
    """Log verbosity and privacy."""

    level: str = "INFO"
    log_transcripts: bool = False


@dataclass(frozen=True)
class Config:
    """The complete daemon configuration."""

    trigger: TriggerConfig
    audio: AudioConfig
    stt: SttConfig
    inject: InjectConfig
    feedback: FeedbackConfig
    logging: LoggingConfig


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "on"}:
            return True
        if lowered in {"false", "no", "0", "off"}:
            return False
    log.warning("expected a boolean, got %r; using %r", value, default)
    return default


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass
    log.warning("expected an integer, got %r; using %r", value, default)
    return default


def _as_str(value: Any, default: str) -> str:
    if isinstance(value, str):
        return value
    log.warning("expected a string, got %r; using %r", value, default)
    return default


def _section(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = data.get(name)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        log.warning("config section [%s] is not a table; ignoring it", name)
        return {}
    return value


def _warn_unknown(table: Mapping[str, Any], name: str, known: set[str]) -> None:
    for key in table:
        if key not in known:
            log.warning("unknown config key %s.%s; ignoring it", name, key)


def _build_trigger(table: Mapping[str, Any]) -> TriggerConfig:
    _warn_unknown(
        table,
        "trigger",
        {
            "keys",
            "min_duration_ms",
            "max_duration_s",
            "cancel_key",
            "ignore_ctrl",
            "ignore_shift",
            "ignore_super",
        },
    )
    defaults = TriggerConfig()
    keys = _as_str(table.get("keys", defaults.keys), defaults.keys).strip().lower()
    if not keys:
        log.warning("[trigger] keys is empty; using %r", defaults.keys)
        keys = defaults.keys
    min_ms = _clamp(
        _as_int(table.get("min_duration_ms", defaults.min_duration_ms), defaults.min_duration_ms),
        *MIN_DURATION_MS_RANGE,
    )
    max_s = _clamp(
        _as_int(table.get("max_duration_s", defaults.max_duration_s), defaults.max_duration_s),
        *MAX_DURATION_S_RANGE,
    )
    cancel = (
        _as_str(table.get("cancel_key", defaults.cancel_key), defaults.cancel_key).strip().lower()
    )
    if not cancel:
        cancel = defaults.cancel_key
    return TriggerConfig(
        keys=keys,
        min_duration_ms=min_ms,
        max_duration_s=max_s,
        cancel_key=cancel,
        ignore_ctrl=_as_bool(table.get("ignore_ctrl", defaults.ignore_ctrl), defaults.ignore_ctrl),
        ignore_shift=_as_bool(
            table.get("ignore_shift", defaults.ignore_shift), defaults.ignore_shift
        ),
        ignore_super=_as_bool(
            table.get("ignore_super", defaults.ignore_super), defaults.ignore_super
        ),
    )


def _build_audio(table: Mapping[str, Any]) -> AudioConfig:
    _warn_unknown(table, "audio", {"device", "debug_save", "debug_dir"})
    defaults = AudioConfig()
    device = _as_str(table.get("device", defaults.device), defaults.device).strip()
    if not device:
        device = defaults.device
    debug_dir = _as_str(table.get("debug_dir", defaults.debug_dir), defaults.debug_dir).strip()
    if not debug_dir:
        debug_dir = defaults.debug_dir
    return AudioConfig(
        device=device,
        debug_save=_as_bool(table.get("debug_save", defaults.debug_save), defaults.debug_save),
        debug_dir=debug_dir,
    )


def _build_stt(table: Mapping[str, Any]) -> SttConfig:
    _warn_unknown(table, "stt", {"language", "keywords", "warmup"})
    defaults = SttConfig()
    language = _as_str(table.get("language", defaults.language), defaults.language).strip().lower()
    if language not in LANGUAGES:
        log.warning("[stt] unsupported language %r; using %r", language, defaults.language)
        language = defaults.language
    raw = table.get("keywords", [])
    if isinstance(raw, str):
        raw = [raw]
    keywords: list[str] = []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, str) and item.strip():
                keywords.append(item.strip())
            else:
                log.warning("[stt] ignoring malformed keyword %r", item)
    else:
        log.warning("[stt] keywords must be a list of strings; ignoring it")
    return SttConfig(
        language=language,
        keywords=tuple(keywords),
        warmup=_as_bool(table.get("warmup", defaults.warmup), defaults.warmup),
    )


def _build_inject(table: Mapping[str, Any]) -> InjectConfig:
    _warn_unknown(
        table,
        "inject",
        {"method", "paste_key", "trailing_space", "restore_clipboard", "terminal_overrides"},
    )
    defaults = InjectConfig()
    method = _as_str(table.get("method", defaults.method), defaults.method).strip().lower()
    if method != "clipboard":
        log.warning("[inject] method %r is not supported in v1; using clipboard", method)
        method = "clipboard"
    paste_key = (
        _as_str(table.get("paste_key", defaults.paste_key), defaults.paste_key).strip().lower()
    )
    if not PASTE_KEY_PATTERN.match(paste_key):
        log.warning("[inject] invalid paste_key %r; using %r", paste_key, defaults.paste_key)
        paste_key = defaults.paste_key
    overrides: dict[str, str] = {}
    raw = table.get("terminal_overrides", {})
    if isinstance(raw, Mapping):
        for window_class, key in raw.items():
            if not isinstance(window_class, str) or not isinstance(key, str):
                log.warning("[inject] ignoring malformed terminal_overrides entry %r", window_class)
                continue
            candidate = key.strip().lower()
            if not PASTE_KEY_PATTERN.match(candidate):
                log.warning("[inject] invalid paste key %r for %r; ignoring it", key, window_class)
                continue
            overrides[window_class.strip().lower()] = candidate
    else:
        log.warning("[inject] terminal_overrides must be a table; ignoring it")
    return InjectConfig(
        method=method,
        paste_key=paste_key,
        trailing_space=_as_bool(
            table.get("trailing_space", defaults.trailing_space), defaults.trailing_space
        ),
        restore_clipboard=_as_bool(
            table.get("restore_clipboard", defaults.restore_clipboard), defaults.restore_clipboard
        ),
        terminal_overrides=overrides,
    )


def _build_feedback(table: Mapping[str, Any]) -> FeedbackConfig:
    _warn_unknown(table, "feedback", {"mode", "volume"})
    defaults = FeedbackConfig()
    mode = _as_str(table.get("mode", defaults.mode), defaults.mode).strip().lower()
    if mode not in FEEDBACK_MODES:
        log.warning("[feedback] unknown mode %r; using %r", mode, defaults.mode)
        mode = defaults.mode
    volume = _clamp(
        _as_int(table.get("volume", defaults.volume), defaults.volume),
        *VOLUME_RANGE,
    )
    return FeedbackConfig(mode=mode, volume=volume)


def _build_logging(table: Mapping[str, Any]) -> LoggingConfig:
    _warn_unknown(table, "logging", {"level", "log_transcripts"})
    defaults = LoggingConfig()
    level = _as_str(table.get("level", defaults.level), defaults.level).strip().upper()
    if level not in LOG_LEVELS:
        log.warning("[logging] unknown level %r; using %r", level, defaults.level)
        level = defaults.level
    return LoggingConfig(
        level=level,
        log_transcripts=_as_bool(
            table.get("log_transcripts", defaults.log_transcripts), defaults.log_transcripts
        ),
    )


def _read_toml(path: Path) -> Mapping[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        log.info("no config file at %s; using defaults", path)
        return {}
    except tomllib.TOMLDecodeError as exc:
        log.warning("could not parse %s (%s); using defaults", path, exc)
        return {}
    except OSError as exc:
        log.warning("could not read %s (%s); using defaults", path, exc)
        return {}


def _apply_env(cfg: Config) -> Config:
    """Environment overrides win over the config file (FR-6.5)."""
    audio = cfg.audio
    stt = cfg.stt
    logging_cfg = cfg.logging

    level = os.environ.get("WHISTLEFLOW_LOG_LEVEL")
    if level:
        candidate = level.strip().upper()
        if candidate in LOG_LEVELS:
            logging_cfg = replace(logging_cfg, level=candidate)
        else:
            log.warning("WHISTLEFLOW_LOG_LEVEL=%r is not a valid level; ignoring it", level)

    language = os.environ.get("WHISTLEFLOW_LANGUAGE")
    if language:
        candidate = language.strip().lower()
        if candidate in LANGUAGES:
            stt = replace(stt, language=candidate)
        else:
            log.warning("WHISTLEFLOW_LANGUAGE=%r is not supported; ignoring it", language)

    device = os.environ.get("WHISTLEFLOW_DEVICE")
    if device:
        audio = replace(audio, device=device.strip() or audio.device)

    return replace(cfg, audio=audio, stt=stt, logging=logging_cfg)


def load_config(path: Path | None = None) -> Config:
    """Load the configuration, falling back to defaults for anything invalid."""
    if path is None:
        env_path = os.environ.get("WHISTLEFLOW_CONFIG")
        path = Path(env_path).expanduser() if env_path else config_path()
    path = Path(path).expanduser()

    data = _read_toml(path)
    cfg = Config(
        trigger=_build_trigger(_section(data, "trigger")),
        audio=_build_audio(_section(data, "audio")),
        stt=_build_stt(_section(data, "stt")),
        inject=_build_inject(_section(data, "inject")),
        feedback=_build_feedback(_section(data, "feedback")),
        logging=_build_logging(_section(data, "logging")),
    )
    return _apply_env(cfg)
