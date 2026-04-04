#!/usr/bin/env bash
# Install script for AI Influencer Live Stream Overlay
# Run once before the first launch: bash install.sh

set -e

echo "=========================================="
echo "  AI Influencer Stream – Setup"
echo "=========================================="

# --- Python check ---
python3 --version >/dev/null 2>&1 || { echo "Python 3 is required"; exit 1; }

# --- pip install ---
echo ""
echo "[1/3] Installing Python dependencies…"
pip install --upgrade pip
pip install -r requirements.txt

# --- Linux: v4l2loopback for virtual camera ---
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
  echo ""
  echo "[2/3] (Linux) Checking v4l2loopback for virtual camera support…"
  if ! lsmod | grep -q v4l2loopback; then
    echo "      v4l2loopback not loaded.  To enable virtual camera in OBS run:"
    echo "      sudo apt-get install v4l2loopback-dkms   # Debian/Ubuntu"
    echo "      sudo modprobe v4l2loopback               # load the module"
  else
    echo "      v4l2loopback already loaded."
  fi
fi

# --- assets dir ---
echo ""
echo "[3/3] Creating assets directory…"
mkdir -p assets

echo ""
echo "=========================================="
echo "  Setup complete!"
echo ""
echo "  Next steps:"
echo "  1. Generate an AI avatar:"
echo "       export ANTHROPIC_API_KEY=sk-ant-..."
echo "       export TOGETHER_API_KEY=...           # or OPENAI_API_KEY"
echo "       python generate_avatar.py"
echo ""
echo "  2. Launch the stream overlay:"
echo "       python main.py"
echo ""
echo "  3. (Optional) Stream to OBS as virtual camera:"
echo "       python main.py --virtual-cam"
echo ""
echo "  4. (Optional) Stream directly to Twitch/YouTube:"
echo "       python main.py --rtmp rtmp://live.twitch.tv/app/<YOUR_KEY>"
echo "=========================================="
