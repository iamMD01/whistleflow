"""Unit tests for the ``--check`` diagnostics report.

Every probe (imports, tools, caches, lock file) is mocked, so the report can be
produced on a machine without X11, without a microphone and without the model
(§10.4, §13.1).
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from whistleflow import diagnostics
from whistleflow.config import (
    AudioConfig,
    Config,
    FeedbackConfig,
    InjectConfig,
    LoggingConfig,
    SttConfig,
    TriggerConfig,
    load_config,
)
from whistleflow.diagnostics import (
    Check,
    collect_checks,
    format_report,
    run_check,
    run_trigger_check,
)


def _module(name: str, **attributes: object) -> types.ModuleType:
    """A stand-in module for the import probes."""
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point every environment-dependent probe at a temporary directory."""
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    (tmp_path / "run").mkdir()
    monkeypatch.setenv("WHISTLEFLOW_CONFIG", str(tmp_path / "config.toml"))
    (tmp_path / "config.toml").write_text('[logging]\nlevel = "INFO"\n', encoding="utf-8")
    return tmp_path


@pytest.fixture
def probes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Mock the import, tool and cache probes of the report."""
    cache = tmp_path / "cache"
    (cache / "whistle" / "2.0.0").mkdir(parents=True)
    (cache / "whistle" / "2.0.0" / "whistle.cact").write_bytes(b"0" * 16)
    (cache / "v3" / "3.3.3").mkdir(parents=True)
    (cache / "v3" / "3.3.3" / "libneedle.so").write_bytes(b"0" * 8)
    monkeypatch.setattr(diagnostics, "CACHE_DIR", cache)

    monkeypatch.setitem(sys.modules, "pynput", _module("pynput", __version__="1.8.2"))
    monkeypatch.setitem(sys.modules, "needle", _module("needle", __version__="3.2.8"))

    sounddevice = _module("sounddevice")
    sounddevice.query_devices = lambda **kwargs: {  # type: ignore[attr-defined]
        "name": "Fake Microphone",
        "default_samplerate": 44100.0,
    }
    monkeypatch.setitem(sys.modules, "sounddevice", sounddevice)

    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        diagnostics.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, f"{argv[0]} 1.2.3", ""),
    )
    return cache


def _by_name(checks: list[Check], name: str) -> Check:
    for check in checks:
        if check.name == name:
            return check
    raise AssertionError(f"no check named {name!r} in {[c.name for c in checks]}")


# ------------------------------------------------------------------- the report


def test_the_report_contains_every_probe(env: Path, probes: Path) -> None:
    """§10.4 lists the rows the report has to contain."""
    checks = collect_checks(load_config(Path(os.environ["WHISTLEFLOW_CONFIG"])))
    names = [check.name for check in checks]

    assert "X11 session" in names
    assert "pynput" in names
    assert "microphone" in names
    assert "needle" in names
    assert "model cache" in names
    assert "engine cache" in names
    assert "config" in names
    assert "lock" in names
    for tool in diagnostics.REQUIRED_TOOLS:
        assert tool in names


def test_all_checks_pass_on_a_healthy_machine(env: Path, probes: Path) -> None:
    """A mocked, complete environment must produce no failures at all."""
    checks = collect_checks(load_config(Path(os.environ["WHISTLEFLOW_CONFIG"])))

    assert [check.name for check in checks if not check.ok] == []


def test_the_session_row_reports_the_environment(env: Path, probes: Path) -> None:
    """The session row shows what it saw, so the user can spot a Wayland login."""
    check = _by_name(collect_checks(load_config()), "X11 session")

    assert check.ok is True
    assert "x11" in check.detail
    assert ":0" in check.detail


def test_a_wayland_session_is_reported_as_a_failure(
    monkeypatch: pytest.MonkeyPatch, env: Path, probes: Path
) -> None:
    """v1 is X11-only, so a Wayland session must be a hard failure."""
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")

    check = _by_name(collect_checks(load_config()), "X11 session")
    assert check.ok is False
    assert check.required is True


def test_a_missing_display_is_reported_as_a_failure(
    monkeypatch: pytest.MonkeyPatch, env: Path, probes: Path
) -> None:
    """Without DISPLAY there is no X server to talk to."""
    monkeypatch.delenv("DISPLAY", raising=False)

    check = _by_name(collect_checks(load_config()), "X11 session")
    assert check.ok is False


def test_the_microphone_row_reports_the_device(env: Path, probes: Path) -> None:
    """§10.4: the default input name and sample rate are useful diagnostics."""
    check = _by_name(collect_checks(load_config()), "microphone")

    assert check.ok is True
    assert "Fake Microphone" in check.detail
    assert "44100" in check.detail


def test_a_failing_microphone_probe_is_reported(
    monkeypatch: pytest.MonkeyPatch, env: Path, probes: Path
) -> None:
    """A probe that raises must become a failed row, not a traceback."""

    def boom(**_kwargs: object) -> dict[str, object]:
        raise RuntimeError("no input device")

    sounddevice = _module("sounddevice")
    sounddevice.query_devices = boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sounddevice", sounddevice)

    check = _by_name(collect_checks(load_config()), "microphone")
    assert check.ok is False
    assert "probe failed" in check.detail


def test_a_failing_import_probe_is_reported(
    monkeypatch: pytest.MonkeyPatch, env: Path, probes: Path
) -> None:
    """A missing dependency shows up as a failed row."""
    # A None entry in sys.modules makes the import raise, like a missing package.
    monkeypatch.setitem(sys.modules, "needle", None)

    check = _by_name(collect_checks(load_config()), "needle")
    assert check.ok is False
    assert "import failed" in check.detail


def test_a_failing_pynput_probe_is_reported(
    monkeypatch: pytest.MonkeyPatch, env: Path, probes: Path
) -> None:
    """pynput is probed through importlib; a missing one is a failed row."""
    monkeypatch.setitem(sys.modules, "pynput", None)

    check = _by_name(collect_checks(load_config()), "pynput")
    assert check.ok is False
    assert "import failed" in check.detail


def test_the_model_cache_row_shows_the_file_size(env: Path, probes: Path) -> None:
    """§10.4: the cached model and engine are reported with their size."""
    checks = collect_checks(load_config())

    model = _by_name(checks, "model cache")
    engine = _by_name(checks, "engine cache")
    assert model.ok is True
    assert "whistle.cact" in model.detail
    assert "MB" in model.detail
    assert engine.ok is True
    assert "libneedle.so" in engine.detail


def test_a_missing_model_cache_is_reported(env: Path, probes: Path) -> None:
    """Without a cached model the daemon cannot start; the row must say so."""
    (probes / "whistle" / "2.0.0" / "whistle.cact").unlink()

    check = _by_name(collect_checks(load_config()), "model cache")
    assert check.ok is False
    assert "not found" in check.detail


def test_a_missing_tool_is_reported(
    monkeypatch: pytest.MonkeyPatch, env: Path, probes: Path
) -> None:
    """A missing xclip/xdotool would break pasting and must be visible."""
    monkeypatch.setattr(
        diagnostics.shutil, "which", lambda name: None if name == "xclip" else f"/usr/bin/{name}"
    )

    check = _by_name(collect_checks(load_config()), "xclip")
    assert check.ok is False
    assert check.detail == "not installed"


def test_the_tool_rows_show_the_version(
    monkeypatch: pytest.MonkeyPatch, env: Path, probes: Path
) -> None:
    """§10.4: the presence and the version of each tool are reported."""
    check = _by_name(collect_checks(load_config()), "xdotool")

    assert check.ok is True
    assert "/usr/bin/xdotool" in check.detail
    assert "xdotool 1.2.3" in check.detail


def test_a_missing_config_file_is_not_a_failure(env: Path, probes: Path) -> None:
    """A missing config file is normal: the defaults are used."""
    Path(os.environ["WHISTLEFLOW_CONFIG"]).unlink()

    check = _by_name(collect_checks(load_config()), "config")
    assert check.ok is True
    assert "using defaults" in check.detail
    assert check.required is False


def test_a_broken_config_file_is_reported(env: Path, probes: Path) -> None:
    """A config file that does not parse must be flagged, not crash the report."""
    Path(os.environ["WHISTLEFLOW_CONFIG"]).write_text("nonsense = = =\n", encoding="utf-8")

    check = _by_name(collect_checks(load_config()), "config")
    assert check.ok is False
    assert check.required is False


def test_a_valid_config_file_is_reported(env: Path, probes: Path) -> None:
    """A parsable config file is reported as such."""
    check = _by_name(collect_checks(load_config()), "config")

    assert check.ok is True
    assert "parsed" in check.detail


def test_a_free_lock_is_reported(env: Path, probes: Path) -> None:
    """No lock file means no other daemon is running."""
    check = _by_name(collect_checks(load_config()), "lock")

    assert check.ok is True
    assert check.detail == "free"


def test_a_locked_lock_file_reports_another_instance(env: Path, probes: Path) -> None:
    """FR-7.2: the lock row tells the user that a daemon is already running."""
    lock_path = Path(os.environ["XDG_RUNTIME_DIR"]) / "whistleflow.lock"
    handle = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check = _by_name(collect_checks(load_config()), "lock")
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        os.close(handle)

    assert check.ok is False
    assert "another instance is running" in check.detail
    assert check.required is False


# ------------------------------------------------------------------- formatting


def test_the_report_marks_ok_fail_and_warn() -> None:
    """A readable three-state report: ok, FAIL for required, warn otherwise."""
    report = format_report(
        [
            Check("alpha", True, "fine"),
            Check("beta", False, "broken"),
            Check("gamma", False, "optional", required=False),
        ]
    )

    assert "Whistleflow environment check" in report
    assert "[ok  ] alpha" in report
    assert "[FAIL] beta" in report
    assert "[warn] gamma" in report


def test_the_report_summarises_the_failures() -> None:
    """The last line tells the user how many required checks failed."""
    report = format_report([Check("alpha", False, "broken"), Check("beta", True, "fine")])

    assert "1 required check(s) failed: alpha" in report


def test_the_report_claims_success_when_everything_passes() -> None:
    """A clean run ends with the reassuring summary line."""
    report = format_report([Check("alpha", True, "fine")])

    assert "All required checks passed." in report


def test_optional_failures_do_not_fail_the_run() -> None:
    """Only required checks decide the exit code (§10.4)."""
    report = format_report(
        [Check("alpha", True, "fine"), Check("beta", False, "meh", required=False)]
    )

    assert "All required checks passed." in report


# -------------------------------------------------------------------- run_check


def test_run_check_returns_zero_when_the_required_checks_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--check`` exits 0 when the essentials are fine."""
    monkeypatch.setattr(diagnostics, "collect_checks", lambda _cfg: [Check("alpha", True, "fine")])

    assert run_check(Config.__new__(Config)) == 0


def test_run_check_returns_one_when_a_required_check_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--check`` exits 1 when an essential is missing."""
    monkeypatch.setattr(
        diagnostics, "collect_checks", lambda _cfg: [Check("alpha", False, "broken")]
    )

    assert run_check(Config.__new__(Config)) == 1


def test_run_check_prints_the_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The report is printed on stdout so the user can read it."""
    monkeypatch.setattr(diagnostics, "collect_checks", lambda _cfg: [Check("alpha", True, "fine")])

    run_check(Config.__new__(Config))
    assert "Whistleflow environment check" in capsys.readouterr().out


# ------------------------------------------------------------------ trigger check


def _config_with_trigger(**trigger: object) -> Config:
    """A config with just a trigger section."""
    return Config(
        trigger=TriggerConfig(**trigger),  # type: ignore[arg-type]
        audio=AudioConfig(),
        stt=SttConfig(),
        inject=InjectConfig(),
        feedback=FeedbackConfig(),
        logging=LoggingConfig(),
    )


@pytest.mark.parametrize(
    ("keys", "ignore_ctrl", "ignore_shift", "ignore_super"),
    [
        ("ctrl+shift+l", False, False, True),
        ("alt+l", True, True, True),
        ("shift+l", True, False, True),
        ("super+space", True, True, False),
        ("ctrl+alt+space", False, True, True),
    ],
)
def test_a_correctly_guarded_chord_fires(
    keys: str, ignore_ctrl: bool, ignore_shift: bool, ignore_super: bool
) -> None:
    """Every modifier in a chord needs its guard switched off."""
    cfg = _config_with_trigger(
        keys=keys,
        ignore_ctrl=ignore_ctrl,
        ignore_shift=ignore_shift,
        ignore_super=ignore_super,
    )

    assert run_trigger_check(cfg) == 0


@pytest.mark.parametrize(
    ("keys", "ignore_ctrl", "ignore_shift", "ignore_super"),
    [
        ("ctrl+shift+l", True, False, True),
        ("shift+l", True, True, True),
        ("super+space", True, True, True),
    ],
)
def test_a_chord_blocked_by_its_own_guard_is_reported(
    keys: str,
    ignore_ctrl: bool,
    ignore_shift: bool,
    ignore_super: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A guard that blocks its own chord is the mistake the check exists for."""
    cfg = _config_with_trigger(
        keys=keys,
        ignore_ctrl=ignore_ctrl,
        ignore_shift=ignore_shift,
        ignore_super=ignore_super,
    )

    assert run_trigger_check(cfg) == 1
    assert "does not fire" in capsys.readouterr().out
