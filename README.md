# AI Influencer Live Stream Overlay

Live stream **as** an AI influencer persona.  Your webcam feeds a real-time
overlay that replaces your face/body with the creator's avatar, and an RVC
voice model converts your microphone input to the creator's voice — switching
both simultaneously when you change creator with a keypress.

## How it works

```
Webcam → Face/Body Detection → Avatar Composite → Preview / Virtual Cam / RTMP
Mic    → Voice Capture       → RVC Inference   → Virtual Speaker / Direct Out
                                    ↑
                      Creator profile  (avatar.png + voice.pth)
```

---

## Creator profiles (recommended setup)

Each creator lives in its own folder under `creators/`:

```
creators/
├── lexi/
│   ├── config.json    ← name, pitch_shift, description
│   ├── avatar.png     ← RGBA portrait (transparent background)
│   ├── voice.pth      ← RVC model weights
│   └── voice.index    ← RVC feature index (optional, improves quality)
└── nova/
    ├── config.json
    ├── avatar.png
    └── voice.pth
```

**config.json**

```json
{
  "name": "Lexi",
  "pitch_shift": -3,
  "description": "Energetic gaming streamer with a bright voice",
  "tags": ["gaming", "anime"]
}
```

`pitch_shift` is in semitones (+/−).  Adjust until the converted voice
matches the creator's natural range (typically −6 to +6 for same-gender,
−12 to +12 for cross-gender).

---

## Quick start

### 1. Install

```bash
bash install.sh
```

For voice conversion, also install one of:

```bash
# Option A – local RVC (GPU strongly recommended)
pip install infer-rvc-python torch

# Option B – use a running Applio / RVC WebUI
#   (already running at http://localhost:7865 by default)
#   no extra packages needed
```

### 2. Add your first creator

```bash
# Scaffold the directory
python manage_creators.py add lexi "Lexi"

# Copy your assets in
cp /path/to/lexi_avatar.png   creators/lexi/avatar.png
cp /path/to/lexi_model.pth    creators/lexi/voice.pth
cp /path/to/lexi_model.index  creators/lexi/voice.index  # optional

# Edit pitch shift if needed
nano creators/lexi/config.json
```

Or generate an AI avatar automatically:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export TOGETHER_API_KEY=...
python manage_creators.py generate lexi --style "cyberpunk gamer"
```

### 3. Launch

```bash
python main.py
```

### 4. (Optional) Output to OBS as a virtual camera

```bash
# Linux one-time setup
sudo modprobe v4l2loopback

python main.py --virtual-cam
```

### 5. (Optional) Stream directly to Twitch / YouTube

```bash
python main.py --rtmp rtmp://live.twitch.tv/app/<YOUR_STREAM_KEY>
```

---

## Switching creators live

| Key | Action |
|-----|--------|
| **N** or **→** | Next creator (avatar + voice switch together) |
| **P** or **←** | Previous creator |
| **1–9** | Jump directly to creator slot |
| **V** | Toggle voice conversion on/off |

When you switch, the avatar crossfades (~12 frames) and the new voice model
loads in a background thread (~150–300 ms latency at next audio chunk boundary).

---

## Voice backends

Tried in this order at startup (and on every creator switch):

| Backend | Requirement | Notes |
|---------|-------------|-------|
| **Local RVC** | `infer-rvc-python` + `.pth` file | Best quality, GPU recommended |
| **Applio API** | Applio / RVC WebUI running | Set `--rvc-api http://localhost:7865` |
| **Passthrough** | nothing | Mic routed unchanged |

### Audio device setup

```bash
# List all audio devices
python manage_creators.py devices
python main.py --list-audio

# Use a specific mic / virtual cable
python main.py --voice-in "USB Microphone" --voice-out "VB-Cable Input"
```

**Linux virtual speaker** (for routing to OBS):

```bash
# PulseAudio
pactl load-module module-null-sink sink_name=virt_speaker sink_properties=device.description="Virtual_Speaker"

# PipeWire / pw-jack
pw-loopback --capture-props='media.class=Audio/Sink' --playback-props='media.class=Audio/Source'
```

---

## Overlay modes

| Mode | Description |
|------|-------------|
| `face` | Avatar tracks and scales to your detected face (head tilt too) |
| `replace` | Body segmentation erases you; avatar appears in your place |
| `overlay` | Avatar fills the entire frame |
| `pip` | Full camera feed, avatar in the top-right corner |

Press **M** to cycle modes live.

---

## All options

```
python main.py --help

Visual:
  --creators-dir DIR    Creator profiles directory (default: creators/)
  --avatar PATH         Single avatar PNG fallback
  --avatar-dir DIR      Directory of avatar PNGs (no voice)
  --mode MODE           face | replace | overlay | pip
  --opacity 0.0–1.0     Avatar transparency (default: 0.92)
  --scale N             Face-mode scale factor (default: 2.6)
  --transition N        Crossfade frames (default: 12)

Camera:
  --camera N            Device index (default: 0)
  --width / --height    Resolution (default: 1280×720)
  --fps N               Frames per second (default: 30)
  --no-flip             Disable mirror flip

Voice:
  --no-voice            Disable voice conversion
  --voice-in DEVICE     Microphone device name or index
  --voice-out DEVICE    Output device name or index
  --rvc-api URL         Applio / RVC WebUI endpoint
  --list-audio          Print audio devices and exit

Output:
  --virtual-cam         Expose as virtual camera (for OBS)
  --rtmp URL            RTMP stream URL
```

---

## Creator manager

```bash
python manage_creators.py list
python manage_creators.py add <slug> [display name]
python manage_creators.py generate <slug> [--style "..."]
python manage_creators.py info <slug>
python manage_creators.py devices
```

---

## Where to get RVC models

- **Applio HuggingFace index**: search `applio rvc models` on Hugging Face
- **RVC Discord community**: community-trained voice models
- **Train your own**: use [Applio](https://github.com/IAHispano/Applio) locally with ~10–30 min of clean audio

The `.pth` file goes in `creators/<slug>/voice.pth`.
The `.index` file (if available) goes in `creators/<slug>/voice.index`.

---

## Requirements

- Python 3.10+
- Webcam
- Microphone (for voice conversion)
- `ANTHROPIC_API_KEY` (for `manage_creators.py generate`)
- One image-gen API key (Together AI / Replicate / OpenAI) for avatar generation
- Linux: `v4l2loopback` for virtual camera
