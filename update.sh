#!/usr/bin/env bash
# update.sh
# ---------
# Pull latest code and restart the bot.
# Run this every time you push changes to GitHub.
#
# Usage:
#   ./update.sh

set -euo pipefail

APP_DIR="/home/ubuntu/mubo"
SERVICE_NAME="mubo"

echo "==> Pulling latest code..."
cd "$APP_DIR"
git pull

echo "==> Installing any new dependencies..."
source venv/bin/activate
pip install -r requirements.txt --quiet

echo "==> Restarting bot..."
sudo systemctl restart "$SERVICE_NAME"

echo "==> Waiting for startup..."
sleep 3
sudo systemctl status "$SERVICE_NAME" --no-pager

echo ""
echo "Done. Live logs:"
echo "  sudo journalctl -u $SERVICE_NAME -f"