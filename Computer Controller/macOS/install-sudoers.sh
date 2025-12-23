#!/bin/bash
# Nodel macOS Computer Controller - Sudoers Installer
# Run with: sudo ./install-sudoers.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SUDOERS_FILE="$SCRIPT_DIR/nodel-mac-controller.sudoers"
DEST="/etc/sudoers.d/nodel-mac-controller"

# Check running as root
if [[ $EUID -ne 0 ]]; then
    echo "Error: This script must be run with sudo"
    echo "Usage: sudo ./install-sudoers.sh"
    exit 1
fi

# Check sudoers file exists
if [[ ! -f "$SUDOERS_FILE" ]]; then
    echo "Error: $SUDOERS_FILE not found"
    exit 1
fi

# Validate sudoers syntax
echo "Validating sudoers syntax..."
if ! visudo -c -f "$SUDOERS_FILE" 2>/dev/null; then
    echo "Error: Invalid sudoers syntax in $SUDOERS_FILE"
    exit 1
fi

# Install
cp "$SUDOERS_FILE" "$DEST"
chmod 440 "$DEST"
chown root:wheel "$DEST"

echo "Installed: $DEST"
echo ""
echo "The following actions now work without password prompts:"
echo "  - WakeOnLAN, AutoRestartOnPowerLoss, WakeForNetworkAccess, PowerNap, TCPKeepAlive"
echo "  - ShutdownUnattended, RestartUnattended"
echo ""
echo "To remove: sudo rm $DEST"
