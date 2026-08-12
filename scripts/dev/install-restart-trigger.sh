#!/usr/bin/env bash
# One-time root setup so the panel can be restarted without sudo.
#
#     sudo bash scripts/dev/install-restart-trigger.sh
#
# Afterwards, restarting is:
#
#     touch /mnt/dev/serverkit/.restart-request
#
# This exists because iterating on the panel meant a privileged restart for every
# backend change, which turned a one-line edit into a hand-off. A systemd .path
# unit grants exactly one capability (restart this unit) instead of a sudoers entry
# covering the whole systemctl binary.
set -euo pipefail

UNIT_DIR=/etc/systemd/system
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${SERVERKIT_INSTALL_DIR:-/mnt/dev/serverkit}"
REQUEST_FILE="$INSTALL_DIR/.restart-request"
OWNER="${SUDO_USER:-$(id -un)}"

if [[ $EUID -ne 0 ]]; then
    echo "Run this with sudo — it writes to $UNIT_DIR." >&2
    exit 1
fi

install -m 0644 "$SRC_DIR/serverkit-restart.path" "$UNIT_DIR/serverkit-restart.path"
install -m 0644 "$SRC_DIR/serverkit-restart.service" "$UNIT_DIR/serverkit-restart.service"

# Point the watcher at this checkout if it is not the default location.
if [[ "$INSTALL_DIR" != "/mnt/dev/serverkit" ]]; then
    sed -i "s|PathModified=.*|PathModified=$REQUEST_FILE|" "$UNIT_DIR/serverkit-restart.path"
    sed -i "s|ExecStart=.*|ExecStart=/usr/bin/systemctl restart serverkit-isolated|" \
        "$UNIT_DIR/serverkit-restart.service"
fi

# The watcher only fires on modification, so the file must exist and be writable by
# the developer who will touch it.
touch "$REQUEST_FILE"
chown "$OWNER" "$REQUEST_FILE"
chmod 0644 "$REQUEST_FILE"

# The copy install writes here as root; keeping it writable by the developer means a
# stale copy can be cleared without sudo. Loading now prefers the in-tree source
# anyway (see plugin_service._prefer_builtin_source), so this is belt-and-braces.
if [[ -d "$INSTALL_DIR/backend/app/plugins" ]]; then
    chown -R "$OWNER" "$INSTALL_DIR/backend/app/plugins"
fi

systemctl daemon-reload
systemctl enable --now serverkit-restart.path

echo
echo "Done. Restart the panel with:"
echo "    touch $REQUEST_FILE"
echo
echo "Watch it happen with:"
echo "    journalctl -u serverkit-restart.service -f"
