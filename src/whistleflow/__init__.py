"""Whistleflow — local push-to-talk dictation for X11.

Hold ``Alt+L``, speak, release, and the transcript is pasted where your cursor is.
Everything runs locally: the Whistle speech-to-text model never leaves the machine.
"""

from __future__ import annotations

import os

__version__ = "0.1.0"

# FR-8.1: these have to be set before `needle` is imported anywhere in the
# process. cactus-needle force-fetches config.json on a cache hit and phones home
# with usage events unless telemetry is off, so pin both here at import time.
os.environ["NEEDLE_TELEMETRY"] = "0"
os.environ["HF_HUB_OFFLINE"] = "1"

__all__ = ["__version__"]
