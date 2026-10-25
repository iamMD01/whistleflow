#!/bin/sh
# whistleflow uninstaller.
#
#   curl -fsSL https://raw.githubusercontent.com/iamMD01/whistleflow/main/uninstall.sh | sh
#
# Removes the daemon, the systemd unit, the configuration and the logs.
# The speech-to-text model is kept unless --purge is given, because it is a
# 18 MB download you would otherwise have to fetch again.
#
# Options:
#   --purge    also delete the cached model
#   --help     show this text

set -eu

SRC_DIR="${WHISTLEFLOW_SRC:-$HOME/.local/src/whistleflow}"
BIN_DIR="$HOME/.local/bin"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
CONFIG_DIR="$CONFIG_HOME/whistleflow"
STATE_DIR="$STATE_HOME/whistleflow"
UNIT_DIR="$HOME/.config/systemd/user"
SERVICE_NAME="whistleflow"

PURGE=0

say()  { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
ok()   { printf '\033[1;32m  ok\033[0m  %s\n' "$*"; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --purge)   PURGE=1; shift ;;
        --help|-h) usage ;;
        *)         die "unknown option: $1 (try --help)" ;;
    esac
done

# ---------------------------------------------------------------- stop it

say "Stopping the daemon"

if command -v systemctl >/dev/null 2>&1 && [ -f "$UNIT_DIR/$SERVICE_NAME.service" ]; then
    systemctl --user disable --now "$SERVICE_NAME.service" >/dev/null 2>&1 || true
    ok "service stopped"
else
    pkill -f "python -m $SERVICE_NAME" >/dev/null 2>&1 || true
    ok "no service was running"
fi

# --------------------------------------------------------------- remove files

say "Removing the files"

rm -f "$UNIT_DIR/$SERVICE_NAME.service"
rm -f "$BIN_DIR/$SERVICE_NAME"
rmdir "$UNIT_DIR" 2>/dev/null || true
ok "service and command removed"

if [ -d "$CONFIG_DIR" ]; then
    rm -rf "$CONFIG_DIR"
    ok "configuration removed"
fi

if [ -d "$STATE_DIR" ]; then
    rm -rf "$STATE_DIR"
    ok "logs removed"
fi

if [ -d "$SRC_DIR" ]; then
    rm -rf "$SRC_DIR"
    ok "source removed"
fi

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload >/dev/null 2>&1 || true
fi

# ------------------------------------------------------------------- the model

if [ "$PURGE" -eq 1 ]; then
    say "Removing the model"
    rm -rf "$HOME/.cache/cactus-needle"
    ok "model cache removed"
else
    say "Keeping the model"
    info "the cached model is still in ~/.cache/cactus-needle"
    info "remove it with --purge, or delete that directory by hand"
fi

printf '\n\033[1;32mwhistleflow has been removed.\033[0m\n\n'
