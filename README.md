# Whistleflow

Local push-to-talk dictation for Linux/X11.

Hold **Ctrl+Shift+L**, speak, release — your words appear at the cursor of whatever
application has focus. There is no window to open, no button to click, and
nothing leaves your machine: the [Cactus Whistle](https://huggingface.co/Cactus-Compute/whistle)
speech-to-text model (16.9 MB) runs locally on the CPU.

```
Ctrl+Shift+L ──► record ──► release ──► transcribe ──► paste at the cursor
```

---

## Requirements

| | |
|---|---|
| OS | Linux with an **X11** session (`XDG_SESSION_TYPE=x11`); developed on Kubuntu 24.04 LTS / KDE Plasma |
| Python | 3.12 (managed by `uv`) |
| Tools | `xclip`, `xdotool`, `xprop`, `paplay`, `notify-send`, PortAudio |
| Python deps | `cactus-needle[mic]`, `numpy`, `sounddevice`, `soundfile`, `soxr`, `pynput`, `rich` |

Wayland is **not** supported: global key capture needs the X11 protocol.

## Install

**One command.** It installs the system packages, the Python environment, the
speech-to-text model, the autostart service and the configuration, then checks
that all of it works:

```sh
curl -fsSL https://raw.githubusercontent.com/iamMD01/whistleflow/main/install.sh | sh
```

A different chord can be chosen with:

```sh
curl -fsSL https://raw.githubusercontent.com/iamMD01/whistleflow/main/install.sh | sh -- --keys "alt+l"
```

To remove it again:

```sh
curl -fsSL https://raw.githubusercontent.com/iamMD01/whistleflow/main/uninstall.sh | sh
```

The uninstaller keeps the cached model unless you add `--purge`, because it is
an 18 MB download.

<details>
<summary>Installing from a clone instead</summary>

```sh
git clone https://github.com/iamMD01/whistleflow.git ~/.local/src/whistleflow
cd ~/.local/src/whistleflow
uv sync
```

The model (16.9 MB) and the inference engine (1.6 MB) download once into
`~/.cache/cactus-needle/`. After that the daemon runs fully offline.

</details>

## Run

```sh
uv run whistleflow
```

The daemon is invisible by design — it listens in the background and answers
with short beeps. `Ctrl+C` stops it.

Check the environment:

```sh
uv run whistleflow --check
```

```
Whistleflow environment check

  [ok  ] X11 session   XDG_SESSION_TYPE=x11, DISPLAY=:0
  [ok  ] pynput        installed
  [ok  ] microphone    default at 44100 Hz
  [ok  ] needle        3.2.8
  [ok  ] model cache   ~/.cache/cactus-needle/whistle/2.0.0/whistle.cact (16.9 MB)
  [ok  ] engine cache  ~/.cache/cactus-needle/v3/3.3.3/libneedle.so (1.6 MB)
  [ok  ] xclip         /usr/bin/xclip
  [ok  ] xdotool       /usr/bin/xdotool — xdotool version 3.20160805.1
  [ok  ] paplay        /usr/bin/paplay — pacat 16.1
  [ok  ] notify-send   /usr/bin/notify-send — notify-send 0.8.3
  [ok  ] config        ~/.config/whistleflow/config.toml (not present, using defaults)
  [ok  ] lock          free

All required checks passed.
```

`--check` exits `0` when everything essential is in place and `1` otherwise.

## Usage

| Action | Result |
|---|---|
| Hold **Ctrl** + **Shift** + **L** | A short beep, recording starts |
| Release **L**, **Shift** or **Ctrl** | A short beep, the utterance is transcribed |
| ~1–2 s later | The text is pasted at the cursor, followed by a space |
| Press **Esc** while recording | The recording is discarded, nothing is pasted |
| Nothing was said | "No speech detected" notification, nothing is pasted |
| Hold for more than 60 s | Recording stops automatically and is transcribed |

The listener is **passive**: the focused application still receives the
keystroke, so a bug in the daemon can never make the key unusable. A chord that
is already a shortcut elsewhere (Ctrl+Shift+L selects all occurrences in VS
Code, for example) will make that application act on it as well. Any chord can
be set in the configuration.

---

## Configuration

`~/.config/whistleflow/config.toml`. Every key is optional; missing keys use the
defaults below. Invalid values fall back to the default with a warning in the
log — they never stop the daemon.

```toml
[trigger]
keys = "ctrl+shift+l"      # the chord that starts a recording
min_duration_ms = 300       # shorter recordings are discarded silently
max_duration_s = 60         # a longer hold is cut off and transcribed
cancel_key = "esc"          # aborts the current recording
ignore_ctrl = false         # must be false when the chord contains ctrl
ignore_shift = false        # must be false when the chord contains shift
ignore_super = true         # Super+Ctrl+Shift+L does not trigger

[audio]
device = "default"          # "default", a device name, or a device index
debug_save = false          # write every utterance to a WAV file
debug_dir = "~/.local/state/whistleflow/audio"

[stt]
language = "en"             # "en", "auto", de, fr, es, it, nl, pl
keywords = []               # words or phrases to bias the model towards
warmup = true               # load the model at startup instead of on first use

[inject]
method = "clipboard"        # clipboard paste is the only method in v1
paste_key = "ctrl+v"        # the shortcut that pastes into the focused window
trailing_space = true       # append a space after the pasted text
restore_clipboard = false   # v1 replaces your clipboard and does not restore it
# Per-window-class override. The class name is lowercased.
# terminal_overrides = { konsole = "ctrl+shift+v", kitty = "ctrl+shift+v" }

[feedback]
mode = "beep"               # beep | notify | none
volume = 20000              # paplay volume, 0..65536

[logging]
level = "INFO"              # DEBUG | INFO | WARNING | ERROR
log_transcripts = false     # true writes the recognised text to the log
```

`feedback.mode` decides how the daemon talks to you:

| Mode | State changes | Errors |
|---|---|---|
| `beep` (default) | short sounds | sound **and** a desktop notification |
| `notify` | desktop notifications | desktop notification |
| `none` | silent | silent |

Environment overrides, which win over the file:

```sh
WHISTLEFLOW_CONFIG=/path/to/config.toml   # use a different config file
WHISTLEFLOW_LOG_LEVEL=DEBUG               # DEBUG | INFO | WARNING | ERROR
WHISTLEFLOW_LANGUAGE=de                   # override [stt] language
WHISTLEFLOW_DEVICE="USB Mic"              # override [audio] device
```

---

## Autostart

The daemon should be running before you need it, so start it at login.

**1. A wrapper script** (no spaces in the path — systemd and `Exec=` both
mis-parse a path with a space in it):

```sh
mkdir -p ~/.local/bin
cat > ~/.local/bin/whistleflow <<'EOF'
#!/bin/sh
exec "$HOME/.local/src/whistleflow/.venv/bin/python" -m whistleflow "$@"
EOF
chmod +x ~/.local/bin/whistleflow
```

**2. A systemd user service** — the recommended way:

```sh
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/whistleflow.service <<'EOF'
[Unit]
Description=Whistleflow — local push-to-talk dictation
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=%h/.local/bin/whistleflow
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now whistleflow.service
```

The user manager already has `DISPLAY` and `XAUTHORITY` in its environment, so
the service inherits them — no import needed. `Restart=on-failure` brings the
daemon back if it ever crashes.

```sh
systemctl --user status whistleflow     # is it running, and what is it using?
systemctl --user restart whistleflow
systemctl --user stop whistleflow
journalctl --user -u whistleflow -f      # follow the log
```

**Alternative: a desktop entry** — no systemd, but no auto-restart either:

```sh
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/whistleflow.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Whistleflow
Comment=Local push-to-talk dictation
Exec=%h/.local/bin/whistleflow
Terminal=false
X-GNOME-Autostart-enabled=true
OnlyShowIn=KDE;
EOF
```

Use **one** of the two, not both — the second instance to start loses the lock
and quits with exit code 2.

---

## Resource usage

Measured on Kubuntu 24.04 (8 cores, 16 GB), from `/proc`.

| | Idle | While recording | Right after transcribing |
|---|---|---|---|
| **RAM (RSS)** | **34–57 MB** | ~same | **92–107 MB** |
| **CPU** | **0.1 %** of one core | 0–8 % | burst of ~550 % (≈5.5 cores) for 0.15–1.4 s |

The memory moves in a cycle: the engine allocates a scratch arena for each
transcription, then hands it back to the OS when it is done. So a long idle
period settles at 34–57 MB, and the `rss=` value logged right after a paste
reads 92–107 MB because that is the top of the cycle. **Nothing leaks** — after
67 recordings the idle figure is still 34 MB.

Other numbers:

| | |
|---|---|
| Peak RAM ever | **107 MB** — the one-off model load at startup |
| RSS right after the model loads | 92 MB |
| RSS after the first few utterances | drops to 34–57 MB |
| PSS (proportional, the honest figure) | 27.6 MB |
| Anonymous (heap) / file-backed (libs) | 22.5 MB / 3.0 MB |
| Virtual (VSZ) | 1.48 GB — reservation only, not real usage |
| Swap | 46.5 MB (cold pages the kernel paged out) |
| Threads | 17 (main, pynput, PortAudio, worker, 8 engine OpenMP threads, Python runtime) |
| Open file descriptors | 7 |
| Network sockets | 0 |
| Log file | ~29 KB after 60 utterances, rotates at 1 MB × 3 |
| Model cache on disk | 17.7 MB |

**Cost per utterance** — roughly one CPU-second per second of speech:

| Hold | Wall time | CPU time |
|---|---|---|
| 2 s | 4.0 s | 0.90 s |
| 10 s | 12.0 s | 9.88 s |

The daemon does no work between utterances: 10 ms of CPU over a measured
10-second idle window is 0.1 % of one core.

### Watching it in the system monitor

The daemon renames its process to `whistleflow`, so it does **not** show up
as "python" among the other interpreters:

```sh
top -p "$(cat /run/user/$(id -u)/whistleflow.lock)"   # live view
htop                                     # search for "whistleflow"
```

In KDE System Monitor (ksysguard) search for **whistleflow**. If it does not
appear, switch to *All Processes* and look for the `whistleflow` command.

With systemd, the numbers are right in the status line:

```
$ systemctl --user status whistleflow
● whistleflow.service - Whistleflow — local push-to-talk dictation
     Active: active (running) since Mon 2026-10-26 09:41:03 IST
   Main PID: 1284 (whistleflow)
      Tasks: 18 (limit: 9076)
     Memory: 30.5M (peak: 96.7M swap: 46.5M swap peak: 65.2M)
        CPU: 13.186s
```

`Memory:` is the current RSS, `peak:` the high-water mark, and `CPU:` the total
CPU time used since the daemon started.

The daemon also logs its own memory and timing on every utterance:

```
whistleflow: pasted 51 chars; release-to-paste 1571 ms; rss=104 MB
```

`rss=` is sampled right after each paste, so it reads the top of the memory
cycle. Watch it fall back over the next few lines to confirm the arena is
being released.

---

## Logs

`~/.local/state/whistleflow/whistleflow.log`, rotated at 1 MB × 3 files.
`~/.local/state/whistleflow/whistleflow.log.1` and `.2` are the older ones.

Every utterance logs its timings, which is how the performance target is checked:

```
whistleflow: recording stopped: 10.21s, 450271 samples
whistleflow: transcribed 87 chars in 1183 ms (ttft=1067.0 ms, decode=95.2 tok/s)
whistleflow: pasted 87 chars; release-to-paste 1392 ms; rss=87.4 MB
```

Transcript text is **never** logged unless `logging.log_transcripts = true`.

---

## Troubleshooting

**Nothing happens when I hold Ctrl+Shift+L.**
Run `uv run whistleflow --check-trigger` — it tells you straight away whether
the configured chord actually fires (a chord that contains a modifier is
silently blocked unless that modifier's `ignore_*` guard is off).
Then run `uv run whistleflow --check` for everything else. If the X11 line fails, the session is not X11.
If the microphone line fails, the device is busy or missing. Make sure the
daemon is actually running: `pgrep -af whistleflow`.

**The daemon says "another instance is already running" but no process is visible.**
The lock file lives in `$XDG_RUNTIME_DIR` (usually `/run/user/1000`) and is
released automatically when the daemon exits, including after a crash. If it
still looks locked, remove `$XDG_RUNTIME_DIR/whistleflow.lock`.

**"Whistleflow is already running".**
A second instance tried to start. That is the single-instance lock doing its
job — find the first one with `pgrep -af whistleflow` and stop it, or reboot.

**The text is not pasted, but the notification says "Copied to clipboard".**
The clipboard worked, the keystroke did not. The focused app probably swallows
`Ctrl+V`. In a terminal, map paste to `Ctrl+V` (Konsole: *Settings → Shortcuts
→ Paste*), or set a per-class override in the config:

```toml
[inject]
terminal_overrides = { konsole = "ctrl+shift+v" }
```

**The first word is missing.**
The stream opens on keydown, so there should be nothing clipped. If it still
happens, your microphone has a slow start-up — that is a v1.1 pre-roll feature.

**A notification said "No speech detected".**
The model heard nothing intelligible. Check the input device in *Settings →
Audio*; a muted or wrong-input microphone produces silence, not an error.

**It is slow.**
The first utterance after startup pays the model load (~1 s). With
`stt.warmup = true` that happens while the daemon starts instead. Steady-state
release-to-paste is ~1.2–2.5 s for a 10 s utterance.

**Paste lands in the wrong window.**
The paste goes to whatever has focus at the moment of injection, not at the
moment you started speaking. Click the target window before you hold the key.

**It costs too much memory.**
Idle RSS is 34–57 MB, peaking at 107 MB while the model loads. See the
the [resource usage](#resource-usage) section above.

---

## Uninstall

```sh
# stop the daemon
systemctl --user disable --now whistleflow.service   # if you used systemd
rm ~/.config/systemd/user/whistleflow.service
pkill -f 'python -m whistleflow'      # if you started it by hand

rm ~/.config/autostart/whistleflow.desktop ~/.local/bin/whistleflow
rm -rf ~/.config/whistleflow ~/.local/state/whistleflow
```

The model cache in `~/.cache/cactus-needle/` is left alone on purpose — it is
the expensive part to download.

---

## Packaging and distribution

### What to pick

| Approach | Command for the user | Good for |
|---|---|---|
| **uv tool** ⭐ | `uv tool install whistleflow` | anyone with `uv` — one command, isolated venv, own binary in `~/.local/bin` |
| **install script** ⭐ | `curl -fsSL …/install.sh \| sh` | anyone, no Python tooling assumed; clones, builds a venv, drops the systemd unit |
| **.deb** | `sudo apt install ./whistleflow.deb` | Ubuntu/KDE users who want a package manager entry |
| **PyInstaller binary** | `./whistleflow` | machines without Python at all |
| Flatpak | — | **avoid**: breaks X11 key capture, sound and the model cache |
| AppImage | `./whistleflow.AppImage` | possible, but needs FUSE and still downloads the model |

For a single machine, `uv` plus the systemd unit above is the simplest setup.

### The one-line installer

`install.sh` in this repository does the whole job: it checks the session is
X11, installs the system packages, installs `uv`, clones the repository, syncs
the environment, downloads the model, writes the configuration and the systemd
unit, and finishes with a report of what it found.

```sh
curl -fsSL https://raw.githubusercontent.com/iamMD01/whistleflow/main/install.sh | sh
```

It is safe to run twice: an existing configuration is left alone and the
repository is updated in place.

### Building a .deb

`nfpm` builds it from a YAML file, no Makefile:

```yaml
# nfpm.yaml
name: whistleflow
version: 0.1.0
arch: amd64
platform: linux
section: utils
depends:
  - xclip
  - xdotool
  - paplay
  - libnotify-bin
  - python3
contents:
  - src: ./dist/venv
    dst: /opt/whistleflow
  - src: ./packaging/whistleflow.desktop
    dst: /usr/share/applications/whistleflow.desktop
scripts:
  postinstall: ./packaging/postinstall.sh
  postremove: ./packaging/postremove.sh
```

```sh
uv build                      # build the wheel
uv run pip install --target ./dist/venv ./dist/*.whl   # a relocatable tree
npx --yes nfpm package --packager deb --config nfpm.yaml
```

The `postinstall` script enables the systemd **user** unit (never a system
unit — it needs the user's own `DISPLAY`).

### Before publishing a package

1. **The model is not in the package.** It is downloaded once on first run
   into `~/.cache/cactus-needle/`. That keeps the package small, but the first
   launch needs a network connection.
2. **Check the model licence** before redistributing the weights
   (`Cactus-Compute/whistle` on Hugging Face). Bundling them would make the
   install work offline, but it means shipping someone else's weights.

---

## Development

```sh
uv run pytest -q                  # unit tests, no hardware needed
uv run ruff check .               # lint
uv run ruff format --check .      # formatting
uv run mypy src                   # type check
```

The source layout:

| File | Responsibility |
|---|---|
| `src/whistleflow/app.py` | daemon wiring: lock, signals, listener, worker, injection |
| `src/whistleflow/state.py` | the trigger state machine — pure logic, no I/O |
| `src/whistleflow/hotkey.py` | pynput listener → state machine |
| `src/whistleflow/audio.py` | microphone capture |
| `src/whistleflow/stt.py` | model wrapper, resampling, text cleanup |
| `src/whistleflow/inject.py` | clipboard and paste keystroke |
| `src/whistleflow/feedback.py` | beeps and notifications |
| `src/whistleflow/config.py` | dataclasses and TOML loading |
| `src/whistleflow/diagnostics.py` | `--check` implementation |

`src/whistle_cli/` is the older file-transcription CLI plan and is kept for
reference; it is not used by the daemon.

The full specification, the acceptance matrix and the roadmap are kept in the
project repository.

## Privacy

- Inference is local. No audio, text or metadata leaves the machine.
- `NEEDLE_TELEMETRY=0` and `HF_HUB_OFFLINE=1` are set before the engine is
  imported.
- Audio is never written to disk unless `audio.debug_save = true`.
- No transcript history is kept.
- Your clipboard is replaced by the dictated text and is **not** restored in v1.
