#!/usr/bin/env bash
# One-time server preparation for the Bush & Bush LiveKit intake agent.
#
#   sudo bash deployment/server-bootstrap.sh
#
# Idempotent: safe to re-run. It never touches .env, which is deliberate -
# the keys live only on the server and must not be recreated from a repo.
set -euo pipefail

APP_DIR=/var/www/bblg-livekit-agent
LOG_DIR=/var/log/bblg-livekit-agent
SERVICE=bblg-livekit-agent.service

echo "==> system packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip rsync

echo "==> directories"
mkdir -p "$APP_DIR" "$LOG_DIR"
chmod 755 "$LOG_DIR"

echo "==> virtual environment"
if [ ! -d "$APP_DIR/.venv" ]; then
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --upgrade pip --quiet
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet

echo "==> .env"
if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo "    created $APP_DIR/.env from the template - FILL IN THE KEYS, then"
  echo "    re-run: systemctl restart $SERVICE"
else
  # The file holds LiveKit, Deepgram, OpenAI and ElevenLabs credentials.
  chmod 600 "$APP_DIR/.env"
fi

echo "==> systemd unit"
cp "$APP_DIR/deployment/$SERVICE" "/etc/systemd/system/$SERVICE"
systemctl daemon-reload
systemctl enable "$SERVICE"

echo
echo "Bootstrap complete."
echo "Start it with:   systemctl start $SERVICE"
echo "Watch it with:   journalctl -u $SERVICE -f"
echo
echo "A healthy start logs a line containing 'registered worker' with the"
echo "same agent_name as the LiveKit dispatch rule. If that line never"
echo "appears, calls will ring and drop."
