#!/usr/bin/env bash
# Installs Pi 5 GPIO Controller for the current user (no root needed except for apt).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
dest="$HOME/.local/share/pi5-gpio"

echo "==> Installing system packages (needs sudo)"
sudo apt-get update
sudo apt-get install -y python3-tk python3-venv python3-pip
# The gpiod Python binding is called python3-libgpiod on Raspberry Pi OS (v1.x on
# Bookworm); some releases also ship python3-gpiod. Either is supported by the app.
sudo apt-get install -y python3-libgpiod || sudo apt-get install -y python3-gpiod

echo "==> Creating virtualenv in $dest"
python3 -m venv --system-site-packages "$dest/venv"
"$dest/venv/bin/pip" install --quiet --upgrade "$here"

mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
ln -sf "$dest/venv/bin/pi5-gpio" "$HOME/.local/bin/pi5-gpio"
sed "s|@EXEC@|$dest/venv/bin/pi5-gpio|" "$here/packaging/pi5-gpio.desktop" \
  > "$HOME/.local/share/applications/pi5-gpio.desktop"

if ! id -nG | tr ' ' '\n' | grep -qx gpio; then
  echo "NOTE: you are not in the 'gpio' group. Run: sudo usermod -aG gpio $USER  (then log out/in)"
fi
echo "==> Done. Start it from the menu (Accessories > Pi 5 GPIO Controller) or run: pi5-gpio"
echo "    Try the simulation first with: pi5-gpio --mock"
