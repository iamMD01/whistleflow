#!/bin/sh
# Enable the systemd *user* unit. Never a system unit: the daemon needs the
# user's own DISPLAY and XAUTHORITY, which only the user session manager has.
set -e

UNIT_DIR="${HOME}/.config/systemd/user"

mkdir -p "${UNIT_DIR}" "${HOME}/.local/bin"

cat > "${UNIT_DIR}/whistleflow.service" <<'EOF'
[Unit]
Description=Whistleflow — local push-to-talk dictation
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=/opt/whistleflow/bin/whistleflow
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

# The wrapper keeps the path free of spaces, which systemd requires.
cat > "${HOME}/.local/bin/whistleflow" <<'EOF'
#!/bin/sh
exec /opt/whistleflow/bin/python -m whistleflow "$@"
EOF
chmod +x "${HOME}/.local/bin/whistleflow"

# Only enable the unit; the first run may need the network for the model.
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload || true
    systemctl --user enable whistleflow.service || true
fi

echo "whistleflow installed."
echo "Start it with:  systemctl --user start whistleflow"
echo "Run once while online to cache the model."
