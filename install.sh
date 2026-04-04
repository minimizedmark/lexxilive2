#!/usr/bin/env bash
# Install script for AI Influencer Live Stream Overlay
# Run once before the first launch: bash install.sh

set -e

echo "=========================================="
echo "  AI Influencer Stream – Setup"
echo "=========================================="

# --- Python check ---
python3 --version >/dev/null 2>&1 || { echo "Python 3 is required"; exit 1; }

# --- Node.js check ---
node --version >/dev/null 2>&1 || { echo "Node.js 18+ is required (https://nodejs.org)"; exit 1; }

# --- pip install ---
echo ""
echo "[1/4] Installing Python dependencies…"
pip install --upgrade pip
pip install -r requirements.txt

# --- Node.js server dependencies ---
echo ""
echo "[2/4] Installing Node.js server dependencies…"
cd server && npm install && cd ..

# --- Linux: v4l2loopback for virtual camera ---
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
  echo ""
  echo "[3/4] (Linux) Checking v4l2loopback for virtual camera support…"
  if ! lsmod | grep -q v4l2loopback; then
    echo "      v4l2loopback not loaded.  To enable virtual camera in OBS run:"
    echo "      sudo apt-get install v4l2loopback-dkms   # Debian/Ubuntu"
    echo "      sudo modprobe v4l2loopback               # load the module"
  else
    echo "      v4l2loopback already loaded."
  fi
else
  echo ""
  echo "[3/4] Skipping v4l2loopback check (Linux only)."
fi

# --- assets dir + .env ---
echo ""
echo "[4/4] Creating assets directory and .env files…"
mkdir -p assets creators

if [ ! -f .env ]; then
  cp .env.example .env
  echo "      Created .env from .env.example — fill in your API keys!"
else
  echo "      .env already exists, skipping."
fi

if [ ! -f server/.env ]; then
  cp server/.env.example server/.env
  echo "      Created server/.env from server/.env.example — fill in Supabase keys!"
else
  echo "      server/.env already exists, skipping."
fi

echo ""
echo "=========================================="
echo "  Setup complete!"
echo ""
echo "  IMPORTANT: Edit .env and server/.env with your API keys before running."
echo ""
echo "  Next steps:"
echo ""
echo "  1. Fill in API keys:"
echo "       nano .env           # ANTHROPIC_API_KEY, ELEVENLABS_API_KEY, etc."
echo "       nano server/.env    # SUPABASE_URL, SUPABASE_SERVICE_KEY"
echo ""
echo "  2. Apply the database schema (one time):"
echo "       Paste supabase/migrations/001_initial.sql into Supabase SQL editor"
echo "       Then run: ALTER PUBLICATION supabase_realtime ADD TABLE stream_commands;"
echo ""
echo "  3. (Optional) Generate an AI avatar:"
echo "       python generate_avatar.py"
echo ""
echo "  4. Start the Node.js backend (or deploy to Railway):"
echo "       cd server && npm start"
echo ""
echo "  5. Launch the stream overlay:"
echo "       source .env && python main.py --auto --twitch <channel>"
echo ""
echo "  6. (Optional) Stream to OBS as virtual camera:"
echo "       source .env && python main.py --auto --virtual-cam"
echo "=========================================="
