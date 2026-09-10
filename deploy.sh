
#!/usr/bin/env bash
# deploy.sh
# ---------
# Run this ONCE on a fresh Oracle Cloud Ubuntu 22.04 VM to set up the bot.
# After that, use update.sh for future deployments.
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh

set -euo pipefail

REPO_URL="https://github.com/KilluaZ01/Mubo"
APP_DIR="/home/ubuntu/mubo"
SERVICE_NAME="mubo"

echo "==> [1/8] Updating system packages..."
sudo apt update && sudo apt upgrade -y

echo "==> [2/8] Installing system dependencies..."
sudo apt install -y \
    python3.11 \
    python3.11-venv \
    python3-pip \
    ffmpeg \
    git \
    curl

# Verify FFmpeg installed
ffmpeg -version | head -1

echo "==> [3/8] Cloning repository..."
if [ -d "$APP_DIR" ]; then
    echo "    Directory exists — pulling latest instead"
    cd "$APP_DIR" && git pull
else
    git clone "$REPO_URL" "$APP_DIR"
    cd "$APP_DIR"
fi

echo "==> [4/8] Creating Python virtual environment..."
python3.11 -m venv venv
source venv/bin/activate

echo "==> [5/8] Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "==> [6/8] Creating assets directory..."
mkdir -p assets/gifs

echo "==> [7/8] Setting up .env file..."
if [ ! -f "$APP_DIR/.env" ]; then
    echo ""
    echo "    *** ACTION REQUIRED ***"
    echo "    No .env file found. Creating from example..."
    cp .env.example .env
    echo ""
    echo "    Edit the .env file now and add your Discord token:"
    echo "      nano $APP_DIR/.env"
    echo ""
    echo "    Then re-run this script or continue manually."
    echo ""
else
    echo "    .env file already exists — skipping"
fi

echo "==> [8/8] Installing systemd service..."
sudo cp mubo.service /etc/systemd/system/${SERVICE_NAME}.service
sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}

echo ""
echo "============================================"
echo "  Setup complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Edit your .env:    nano $APP_DIR/.env"
echo "  2. Start the bot:     sudo systemctl start $SERVICE_NAME"
echo "  3. Check status:      sudo systemctl status $SERVICE_NAME"
echo "  4. View logs:         sudo journalctl -u $SERVICE_NAME -f"
echo ""