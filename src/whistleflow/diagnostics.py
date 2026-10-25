"""``whistleflow --check`` — a readable pass/fail report on the environment.

Answers "why is nothing happening?" without starting the daemon or loading
the model (§10.4).
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import Config, config_path, lock_path
from .state import State, StateMachine

CACHE_DIR = Path.home() / ".cache" / "cactus-needle"

REQUIRED_TOOLS = ("xclip", "xdotool", "paplay", "notify-send")

# xclip has no --version; it spells it -version.
_VERSION_ARGS = {
    "xclip": ["xclip", "-version"],
    "xdotool": ["xdotool", "--version"],
    "paplay": ["paplay", "--version"],
    "notify-send": ["notify-send", "--version"],
}


@dataclass(frozen=True)
class Check:
    """One line of the report."""

    name: str
    ok: bool
    detail: str
    required: bool = True


def _model_cache() -> Path | None:
    candidates = sorted(CACHE_DIR.glob("whistle/*/whistle.cact"))
    return candidates[-1] if candidates else None


def _engine_cache() -> Path | None:
    candidates = sorted(CACHE_DIR.glob("v*/*/libneedle.so"))
    return candidates[-1] if candidates else None


def _file_check(name: str, path: Path | None, hint: str) -> Check:
    if path is None:
        return Check(name, False, f"not found — {hint}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        return Check(name, False, f"{path} ({exc})")
    return Check(name, True, f"{path} ({size / 1_000_000:.1f} MB)")


def _tool_check(name: str) -> Check:
    path = shutil.which(name)
    if path is None:
        return Check(name, False, "not installed")
    version = ""
    try:
        result = subprocess.run(
            _VERSION_ARGS.get(name, [name, "--version"]),
            timeout=2,
            check=False,
            capture_output=True,
            text=True,
        )
        output = (result.stdout or result.stderr).strip()
        if "No such file" not in output:
            lines = output.splitlines()
            version = lines[0] if lines else ""
    except (subprocess.TimeoutExpired, OSError):
        version = ""
    detail = f"{path}{f' — {version}' if version else ''}"
    return Check(name, True, detail)


def collect_checks(cfg: Config) -> list[Check]:
    """Run every probe and return the report rows."""
    checks: list[Check] = []

    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    display = os.environ.get("DISPLAY", "unset")
    ok = session == "x11" and display != "unset"
    checks.append(
        Check(
            "X11 session",
            ok,
            f"XDG_SESSION_TYPE={session}, DISPLAY={display}",
            required=True,
        )
    )

    try:
        module = importlib.import_module("pynput")
        version = getattr(module, "__version__", "installed")
        checks.append(Check("pynput", True, f"{version}"))
    except Exception as exc:  # noqa: BLE001 - any import error is worth reporting
        checks.append(Check("pynput", False, f"import failed: {exc}"))

    try:
        import sounddevice as sd

        info = sd.query_devices(kind="input")
        checks.append(
            Check(
                "microphone",
                True,
                f"{info['name']} at {int(info['default_samplerate'])} Hz",
            )
        )
    except Exception as exc:  # noqa: BLE001 - any probe error is worth reporting
        checks.append(Check("microphone", False, f"probe failed: {exc}"))

    try:
        import needle

        checks.append(Check("needle", True, getattr(needle, "__version__", "unknown")))
    except Exception as exc:  # noqa: BLE001 - any import error is worth reporting
        checks.append(Check("needle", False, f"import failed: {exc}"))

    checks.append(_file_check("model cache", _model_cache(), "run the daemon once while online"))
    checks.append(_file_check("engine cache", _engine_cache(), "run the daemon once while online"))

    for tool in REQUIRED_TOOLS:
        checks.append(_tool_check(tool))

    path = Path(os.environ.get("WHISTLEFLOW_CONFIG") or config_path()).expanduser()
    if not path.exists():
        checks.append(
            Check("config", True, f"{path} (not present, using defaults)", required=False)
        )
    else:
        try:
            with path.open("rb") as handle:
                tomllib.load(handle)
            checks.append(Check("config", True, f"{path} parsed", required=False))
        except (tomllib.TOMLDecodeError, OSError) as exc:
            checks.append(Check("config", False, f"{path}: {exc}", required=False))

    lock_file = lock_path()
    if not lock_file.exists():
        checks.append(Check("lock", True, "free", required=False))
    else:
        try:
            import fcntl

            fd = os.open(lock_file, os.O_RDWR)
        except OSError as exc:
            checks.append(Check("lock", False, f"{lock_file}: {exc}", required=False))
        else:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                checks.append(Check("lock", False, "another instance is running", required=False))
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
                checks.append(Check("lock", True, "free", required=False))
            finally:
                os.close(fd)

    return checks


def format_report(checks: list[Check]) -> str:
    """Render the report as plain text."""
    width = max(len(check.name) for check in checks)
    lines = ["Whistleflow environment check", ""]
    for check in checks:
        mark = "ok  " if check.ok else "FAIL"
        if not check.ok and not check.required:
            mark = "warn"
        lines.append(f"  [{mark}] {check.name.ljust(width)}  {check.detail}")
    failed = [check for check in checks if check.required and not check.ok]
    lines.append("")
    if failed:
        lines.append(
            f"{len(failed)} required check(s) failed: " + ", ".join(c.name for c in failed)
        )
    else:
        lines.append("All required checks passed.")
    return "\n".join(lines)


def run_trigger_check(cfg: Config) -> int:
    """Verify that the configured chord actually arms the recorder.

    A chord that contains a guard modifier is silently blocked unless that
    guard is switched off, which is easy to get wrong in the config file.
    """
    machine = StateMachine(cfg.trigger)
    for part in cfg.trigger.keys.split("+"):
        machine.on_key_down(part)
    if machine.state is not State.RECORDING:
        print(f"the chord {cfg.trigger.keys!r} does not fire")
        print("every modifier in the chord needs its ignore_* guard set to false")
        return 1
    print(f"the chord {cfg.trigger.keys!r} fires")
    return 0


def run_check(cfg: Config) -> int:
    """Print the report. Returns 0 when the essentials pass, 1 otherwise."""
    checks = collect_checks(cfg)
    print(format_report(checks))
    return 0 if all(check.ok for check in checks if check.required) else 1
