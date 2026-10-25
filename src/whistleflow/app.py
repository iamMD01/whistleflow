"""Daemon wiring: lock file, signals, listener, recorder, worker and injection.

One process, four concurrency domains (§7.1):

* the main thread owns the lifecycle and waits for shutdown;
* the pynput thread turns key events into state machine actions;
* the PortAudio callback only appends copied chunks;
* a single worker thread does model calls and injection, because the engine is
  not thread-safe.
"""

from __future__ import annotations

import fcntl
import logging
import os
import queue
import signal
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import FrameType

import numpy as np

from .audio import AudioError, Recorder, save_debug_wav
from .config import Config, LoggingConfig, lock_path, log_path
from .feedback import Feedback
from .hotkey import Event, HotkeyListener
from .inject import InjectError, Injector
from .state import Action, StateMachine
from .stt import Transcriber, to_16k

log = logging.getLogger(__name__)

LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3

# How long the worker gets to finish the current utterance on shutdown.
SHUTDOWN_GRACE_S = 5.0


def setup_logging(cfg: LoggingConfig) -> None:
    """Log to stderr and to a rotating file (FR-7.6)."""
    level = getattr(logging, cfg.level, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        rotating = RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
        rotating.setFormatter(formatter)
        root.addHandler(rotating)
    except OSError as exc:
        root.warning("could not open the log file: %s", exc)


def _rss_mb() -> float:
    """Current resident memory in megabytes, or -1 when unavailable."""
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        pass
    return -1.0


def set_process_name(name: str = "whistleflow") -> None:
    """Rename the process so it is findable as "whistleflow".

    The interpreter is called "python" in ps, top and the KDE system monitor,
    which makes the daemon hard to spot among a dozen other python processes.
    PR_SET_NAME rewrites the kernel's name for the main thread, which is what
    those tools display.
    """
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(15, ctypes.c_char_p(name.encode()), 0, 0, 0)  # PR_SET_NAME
    except Exception as exc:  # noqa: BLE001 - purely cosmetic
        log.debug("could not set the process name: %s", exc)


class WhistleflowApp:
    """Wires the pieces together and runs the daemon."""

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._sm = StateMachine(cfg.trigger)
        self._recorder = Recorder(cfg.audio, max_duration_s=cfg.trigger.max_duration_s)
        self._transcriber = Transcriber(cfg.stt)
        self._injector = Injector(cfg.inject)
        self._feedback = Feedback(cfg.feedback)
        self._jobs: queue.Queue[np.ndarray] = queue.Queue(maxsize=1)
        self._shutdown = threading.Event()
        self._lock = threading.RLock()
        self._max_timer: threading.Timer | None = None
        self._lock_fd: int | None = None
        self._released_at = 0.0

    # ------------------------------------------------------------------ setup

    def _acquire_lock(self) -> bool:
        """Take the single-instance lock. False means another daemon owns it."""
        path = lock_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            log.warning("lock file unusable (%s); running without a single-instance guard", exc)
            return True
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._lock_fd = fd
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode())
        except OSError:
            pass
        log.debug("lock acquired: %s", path)
        return True

    def _release_lock(self) -> None:
        if self._lock_fd is None:
            return
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(self._lock_fd)
        except OSError:
            pass
        self._lock_fd = None

    def _install_signal_handlers(self) -> None:
        def handler(signum: int, frame: FrameType | None) -> None:
            log.info("received signal %s; shutting down", signal.Signals(signum).name)
            self._shutdown.set()

        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(signum, handler)
            except ValueError:
                log.debug("could not install a handler for signal %s", signum)

    # ------------------------------------------------------------------- run

    def run(self) -> int:
        """Run the daemon. Returns the process exit code."""
        set_process_name()
        setup_logging(self._cfg.logging)
        log.info("whistleflow starting (pid=%d, rss=%.0f MB)", os.getpid(), _rss_mb())

        if not self._acquire_lock():
            log.warning("another instance is already running")
            self._feedback.error("Whistleflow is already running")
            return 2

        self._install_signal_handlers()

        try:
            if self._cfg.stt.warmup:
                self._transcriber.warmup()
        except Exception as exc:  # any load failure is user facing
            log.exception("could not load the model")
            self._feedback.error(f"Whistle model not cached or unusable: {exc}")
            self._release_lock()
            return 1

        worker = threading.Thread(target=self._worker_loop, name="whistleflow-worker", daemon=True)
        worker.start()

        listener = HotkeyListener(self._cfg.trigger.keys, self._sm, self._on_event)
        try:
            listener.start()
        except Exception as exc:  # any X error is user facing
            log.exception("could not start the hotkey listener")
            self._feedback.error(f"X11 unavailable: {exc}")
            self._release_lock()
            return 1

        log.info("whistleflow ready (rss=%.0f MB)", _rss_mb())
        try:
            while not self._shutdown.is_set():
                self._shutdown.wait(0.5)
        finally:
            listener.stop()
            self._cancel_max_timer()
            try:
                self._recorder.cancel()
            except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                log.debug("could not close the recorder: %s", exc)
            worker.join(timeout=SHUTDOWN_GRACE_S)
            self._release_lock()
            log.info("whistleflow stopped (rss=%.0f MB)", _rss_mb())
        return 0

    # ---------------------------------------------------------------- events

    def _on_event(self, event: Event) -> None:
        """Handle a state machine action. Called from the pynput thread."""
        if event.action is Action.START_RECORDING:
            self._start_recording()
        elif event.action is Action.STOP_RECORDING:
            self._stop_recording()
        elif event.action is Action.CANCEL_RECORDING:
            self._cancel_recording()

    def _start_recording(self) -> None:
        with self._lock:
            try:
                self._recorder.start()
            except AudioError as exc:
                log.error("microphone unavailable: %s", exc)
                self._feedback.error(f"Microphone unavailable: {exc}")
                self._sm.abort_recording()
                return
            self._feedback.start()
            self._arm_max_timer()

    def _stop_recording(self) -> None:
        with self._lock:
            self._cancel_max_timer()
            samples = self._recorder.stop()
            duration = self._recorder.duration_s
            self._released_at = time.monotonic()
        self._feedback.stop()
        log.info("recording stopped: %.2fs, %d samples", duration, samples.size)

        if samples.size == 0:
            log.info("no audio captured; discarding")
            self._sm.finish_busy()
            return
        if duration < self._cfg.trigger.min_duration_ms / 1000:
            log.info("recording shorter than %d ms; discarding", self._cfg.trigger.min_duration_ms)
            self._sm.finish_busy()
            return

        try:
            self._jobs.put_nowait(samples)
        except queue.Full:
            log.warning("the worker is still busy; dropping this utterance")
            self._sm.finish_busy()

    def _cancel_recording(self) -> None:
        with self._lock:
            self._cancel_max_timer()
            self._recorder.cancel()
        log.info("recording cancelled")
        self._feedback.cancel()
        self._sm.abort_recording()

    # ----------------------------------------------------------------- timer

    def _arm_max_timer(self) -> None:
        self._cancel_max_timer()
        timer = threading.Timer(
            float(self._cfg.trigger.max_duration_s),
            self._on_max_duration,
        )
        timer.daemon = True
        self._max_timer = timer
        timer.start()

    def _cancel_max_timer(self) -> None:
        timer = self._max_timer
        self._max_timer = None
        if timer is not None:
            timer.cancel()

    def _on_max_duration(self) -> None:
        log.warning("reached the %ds limit; finalising", self._cfg.trigger.max_duration_s)
        with self._lock:
            if not self._recorder.recording:
                return
            self._cancel_max_timer()
            samples = self._recorder.stop()
            duration = self._recorder.duration_s
            self._released_at = time.monotonic()
            # Leave RECORDING for BUSY so that releasing the key later does not
            # stop a second time.
            self._sm.auto_stop()
        self._feedback.stop()
        log.info("auto-stopped: %.2fs, %d samples", duration, samples.size)
        if samples.size == 0:
            self._sm.finish_busy()
            return
        try:
            self._jobs.put_nowait(samples)
        except queue.Full:
            self._sm.finish_busy()

    # ---------------------------------------------------------------- worker

    def _worker_loop(self) -> None:
        """The single thread that owns the model and the clipboard."""
        while not self._shutdown.is_set():
            try:
                samples = self._jobs.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self._process(samples)
            except Exception:  # one bad utterance must not kill the worker
                log.exception("unexpected failure while processing an utterance")
                self._feedback.error("Something went wrong while transcribing")
            finally:
                self._jobs.task_done()
                self._sm.finish_busy()

    def _process(self, samples: np.ndarray) -> None:
        started = time.perf_counter()
        try:
            audio = to_16k(samples, self._recorder.rate)
            if self._cfg.audio.debug_save:
                self._save_debug(audio)
            result = self._transcriber.transcribe(audio)
        except Exception as exc:  # the error is reported to the user
            log.exception("transcription failed")
            self._feedback.error(f"Transcription failed: {exc}")
            return

        transcribe_ms = (time.perf_counter() - started) * 1000
        text = self._transcriber.postprocess(result)
        log.info(
            "transcribed %d chars in %.0f ms (ttft=%s ms, decode=%s tok/s)",
            len(text),
            transcribe_ms,
            result.get("ttft_ms"),
            result.get("decode_tps"),
        )
        if self._cfg.logging.log_transcripts:
            log.info("transcript: %s", text)

        if not text:
            log.info("empty transcript; nothing to paste")
            self._feedback.error("No speech detected")
            return

        try:
            self._injector.paste(text)
        except InjectError as exc:
            log.error("paste failed: %s", exc)
            self._feedback.error("Copied to clipboard — press Ctrl+V")
            return

        total_ms = (time.monotonic() - self._released_at) * 1000 if self._released_at else 0.0
        log.info(
            "pasted %d chars; release-to-paste %.0f ms; rss=%.0f MB",
            len(text),
            total_ms,
            _rss_mb(),
        )

    def _save_debug(self, audio: np.ndarray) -> None:
        try:
            directory = Path(self._cfg.audio.debug_dir).expanduser()
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = directory / f"whistleflow-{stamp}.wav"
            save_debug_wav(path, audio)
            log.info("debug audio written to %s", path)
        except Exception as exc:  # noqa: BLE001 - debug audio is best effort
            log.warning("could not write the debug audio: %s", exc)


def run_daemon(cfg: Config) -> int:
    """Convenience wrapper used by the CLI."""
    return WhistleflowApp(cfg).run()
