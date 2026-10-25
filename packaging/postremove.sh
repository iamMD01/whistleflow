#!/bin/sh
# Remove the systemd user unit and the wrapper. The model cache and the user's
# config are left alone: the model is an expensive download and the config may
# hold personal keywords.
set -e

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now whistleflow.service || true
fi

rm -f "${HOME}/.config/systemd/user/whistleflow.service"
rm -f "${HOME}/.local/bin/whistleflow"
rmdir "${HOME}/.config/systemd/user" 2>/dev/null || true
