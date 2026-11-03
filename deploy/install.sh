#!/usr/bin/env bash
# One-command installer for a free Ubuntu/Debian server (Oracle Cloud Always Free, Google Cloud e2-micro…)
#
#   curl -fsSL https://raw.githubusercontent.com/deshawnclark116-prog/pump/main/deploy/install.sh | bash
#
# Re-run it any time to update to the latest code. It keeps your settings and data.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/deshawnclark116-prog/pump.git}"
BRANCH="${BRANCH:-main}"
DIR="${DIR:-$HOME/pump}"
SERVICE=memeradar
RUN_USER="${USER:-$(id -un)}"

echo "==> Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip git >/dev/null

echo "==> Getting the code ($BRANCH)"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch -q origin "$BRANCH"
  git -C "$DIR" checkout -q "$BRANCH"
  git -C "$DIR" reset -q --hard "origin/$BRANCH"
else
  git clone -q --branch "$BRANCH" "$REPO_URL" "$DIR"
fi
cd "$DIR"

echo "==> Python environment"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  # can be passed in up front:  NTFY_TOPIC=... bash install.sh
  NTFY="${NTFY_TOPIC:-}"; DISCORD="${DISCORD_WEBHOOK_URL:-}"; TG_TOKEN="${TELEGRAM_BOT_TOKEN:-}"; TG_CHAT="${TELEGRAM_CHAT_ID:-}"
  if [ -z "$NTFY$DISCORD$TG_TOKEN" ] && { : </dev/tty; } 2>/dev/null; then
    echo "==> Where should alerts go? (press Enter to skip any)"
    read -r -p "ntfy topic name: " NTFY </dev/tty || true
    read -r -p "Discord webhook URL: " DISCORD </dev/tty || true
    read -r -p "Telegram bot token: " TG_TOKEN </dev/tty || true
    read -r -p "Telegram chat id: " TG_CHAT </dev/tty || true
  fi
  umask 077
  cat > .env <<ENV
NTFY_TOPIC=${NTFY}
DISCORD_WEBHOOK_URL=${DISCORD}
TELEGRAM_BOT_TOKEN=${TG_TOKEN}
TELEGRAM_CHAT_ID=${TG_CHAT}
MEMERADAR_DB=${DIR}/memeradar.db
ENV
fi
[ -f config.toml ] || cp config.example.toml config.toml

echo "==> Background service (starts on boot, restarts if it crashes)"
sudo tee /etc/systemd/system/$SERVICE.service >/dev/null <<UNIT
[Unit]
Description=memeradar meme-coin radar
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$DIR
EnvironmentFile=$DIR/.env
ExecStart=$DIR/.venv/bin/python -m memeradar run
Restart=always
RestartSec=15
MemoryMax=600M

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable -q $SERVICE
sudo systemctl restart $SERVICE
sleep 3
systemctl --no-pager --lines=0 status $SERVICE | head -3 || true

cat <<DONE

Done. The radar is running in the background and survives reboots.

  See it working:    journalctl -u $SERVICE -f
  Alert results:     cd $DIR && set -a && . ./.env && set +a && .venv/bin/python -m memeradar report
  Change alerts:     nano $DIR/.env   then   sudo systemctl restart $SERVICE
  Change settings:   nano $DIR/config.toml   then   sudo systemctl restart $SERVICE
  Update the code:   run this installer again
  Stop it:           sudo systemctl stop $SERVICE
DONE
