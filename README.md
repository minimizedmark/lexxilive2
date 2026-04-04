# AI Influencer Live Stream Overlay

Captures your webcam and superimposes an AI-generated influencer avatar over the live video in real time.  The camera still sees you at the keyboard — the AI persona is composited on top.

## How it works

```
Webcam → Face/Body Detection → Avatar Compositing → Preview Window
                                                   → Virtual Camera (OBS)
                                                   → RTMP Stream (Twitch/YouTube)
```

## Overlay modes

| Mode | Description |
|------|-------------|
| `face` | Avatar tracks and scales to your detected face |
| `replace` | Body segmentation erases you and puts the avatar in your place |
| `overlay` | Avatar fills the entire frame (you show through based on opacity) |
| `pip` | Your real camera full-frame, avatar in the top-right corner |

Press **M** in the preview window to cycle modes live.

---

## Quick start

### 1. Install

```bash
bash install.sh
```

### 2. Generate an AI avatar

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export TOGETHER_API_KEY=...        # get a free key at https://api.together.xyz
python generate_avatar.py
```

Or supply any portrait PNG with a transparent background:

```bash
cp /path/to/my_influencer.png assets/avatar.png
```

### 3. Launch

```bash
python main.py
```

---

## Options

```
python main.py --help

  --avatar PATH       PNG avatar file (default: assets/avatar.png)
  --camera N          Camera device index (default: 0)
  --width / --height  Output resolution (default: 1280x720)
  --fps N             Frames per second (default: 30)
  --mode MODE         face | replace | overlay | pip (default: face)
  --opacity 0.0-1.0   Avatar transparency (default: 0.92)
  --scale N           Avatar size relative to face in face mode (default: 2.6)
  --no-flip           Disable horizontal mirror flip
  --virtual-cam       Expose as a virtual camera device (use in OBS)
  --rtmp URL          Stream directly, e.g. rtmp://live.twitch.tv/app/<key>
```

---

## Streaming to OBS

1. Run with `--virtual-cam`
2. Linux only (one-time):
   ```bash
   sudo apt-get install v4l2loopback-dkms
   sudo modprobe v4l2loopback
   ```
3. In OBS, add a **Video Capture Device** source and select the virtual camera

## Streaming directly to Twitch / YouTube

```bash
# Twitch
python main.py --rtmp rtmp://live.twitch.tv/app/<YOUR_STREAM_KEY>

# YouTube
python main.py --rtmp rtmp://a.rtmp.youtube.com/live2/<YOUR_STREAM_KEY>
```

---

## Keyboard controls (preview window)

| Key | Action |
|-----|--------|
| Q / ESC | Quit |
| M | Cycle overlay mode |
| + / = | Increase opacity |
| - | Decrease opacity |
| R | Reload avatar from disk (hot-swap) |
| S | Save screenshot |
| H | Toggle help overlay |

---

## Avatar generation options

```bash
python generate_avatar.py --style "cyberpunk gamer"
python generate_avatar.py --style "luxury fashion"
python generate_avatar.py --out assets/second_avatar.png
python generate_avatar.py --prompt "your own SD prompt"
python generate_avatar.py --no-remove-bg   # keep background
```

Image generation backends (set one API key):

| Backend | Env var | Notes |
|---------|---------|-------|
| Together AI FLUX | `TOGETHER_API_KEY` | Fast, free tier available |
| Replicate SDXL | `REPLICATE_API_TOKEN` | Pay per run |
| OpenAI DALL-E 3 | `OPENAI_API_KEY` | High quality |

---

## Requirements

- Python 3.10+
- Webcam
- `ANTHROPIC_API_KEY` for persona generation
- One image-gen API key for `generate_avatar.py`
- Linux: `v4l2loopback` for virtual camera support
