#!/usr/bin/env bash
# Linux only: installs deps, venv, and a systemd service that starts the agent on boot.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
sudo apt update && sudo apt install -y python3-venv portaudio19-dev
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -r requirements/base.txt
[ -f .env ] || { cp .env.example .env; echo "Created .env, fill in your keys then re-run this script."; exit 1; }
.venv/bin/python main.py --check
sed -e "s|__USER__|$USER|g" -e "s|__DIR__|$DIR|g" -e "s|__UID__|$(id -u)|g" deploy/room-agent.service \
  | sudo tee /etc/systemd/system/room-agent.service >/dev/null
# keep the user's audio session alive without a login
sudo loginctl enable-linger "$USER"
sudo systemctl daemon-reload
sudo systemctl enable --now room-agent
echo "Running. Logs: journalctl -u room-agent -f"
