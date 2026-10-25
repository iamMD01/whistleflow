"""Command line entry point for whistleflow."""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .config import load_config
from .diagnostics import run_check, run_trigger_check


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="whistleflow",
        description="Local push-to-talk dictation for X11. Hold Alt+L, speak, release.",
    )
    parser.add_argument("--version", action="version", version=f"whistleflow {__version__}")
    parser.add_argument(
        "--check",
        action="store_true",
        help="print an environment diagnostics report and exit",
    )
    parser.add_argument(
        "--check-trigger",
        action="store_true",
        help="verify that the configured trigger chord fires and exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Bootstrap logging so that config problems are visible before the daemon
    # installs its own handlers.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    cfg = load_config()
    if args.check:
        return run_check(cfg)
    if args.check_trigger:
        return run_trigger_check(cfg)
    from .app import run_daemon

    return run_daemon(cfg)


if __name__ == "__main__":
    sys.exit(main())
