#!/usr/bin/env python3
"""
AI Influencer Live Stream Overlay
==================================
Captures your webcam, superimposes an AI influencer avatar, and converts
your voice in real time to match that creator's voice.

Avatar AND voice switch together when you press N / P / 1-9.

Creator mode (recommended)
  Organise each persona as a sub-folder under  creators/  :
    creators/
    └── lexi/
        ├── config.json     ← name, pitch_shift, description
        ├── avatar.png      ← RGBA portrait PNG
        ├── voice.pth       ← RVC voice model
        └── voice.index     ← RVC index (optional)

  Then just run:  python main.py

Fallback (no creator folders)
  python main.py --avatar my_avatar.png
  python main.py --avatar-dir avatars/

Generate an AI avatar:
  python generate_avatar.py

Add a new creator scaffold:
  python manage_creators.py add lexi "Lexi"

Voice backends (tried in order):
  1. Local RVC   – pip install infer-rvc-python   (+ GPU recommended)
  2. Applio API  – point --rvc-api to your running Applio/RVC WebUI
  3. Passthrough – mic routed unchanged (no .pth file needed)

Keyboard controls:
  Q / ESC   Quit
  M         Cycle overlay mode  (face → replace → overlay → pip)
  N / →     Next creator
  P / ←     Previous creator
  1–9       Jump to creator slot
  V         Toggle voice conversion on/off
  +  /  =   Increase opacity
  -          Decrease opacity
  R          Reload current avatar from disk
  S          Save screenshot
  H          Toggle help overlay
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.stream_overlay import AIInfluencerStream, MODES
from src.voice import VoiceEngine


def parse_args():
    p = argparse.ArgumentParser(
        description='AI Influencer Live Stream Overlay',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # ---- Visual ----
    vis = p.add_argument_group('Visual')
    vis.add_argument('--creators-dir', default='creators',
                     help='Directory containing creator sub-folders (default: creators/)')
    vis.add_argument('--avatar', default='assets/avatar.png',
                     help='Single avatar PNG (used when no creators/ folder exists)')
    vis.add_argument('--avatar-dir', default='',
                     help='Directory of avatar PNGs (no voice, switchable via N/P/1-9)')
    vis.add_argument('--mode', default='face', choices=MODES,
                     help='Overlay mode: face | replace | overlay | pip  (default: face)')
    vis.add_argument('--opacity', type=float, default=0.92,
                     help='Avatar opacity 0.0–1.0 (default: 0.92)')
    vis.add_argument('--scale', type=float, default=2.6,
                     help='Avatar scale relative to detected face (default: 2.6)')
    vis.add_argument('--transition', type=int, default=12,
                     help='Crossfade frames when switching creators (default: 12)')

    # ---- Camera ----
    cam = p.add_argument_group('Camera')
    cam.add_argument('--camera', type=int, default=0,
                     help='Camera device index (default: 0)')
    cam.add_argument('--width',  type=int, default=1280)
    cam.add_argument('--height', type=int, default=720)
    cam.add_argument('--fps',    type=int, default=30)
    cam.add_argument('--no-flip', action='store_true',
                     help='Disable horizontal mirror flip')

    # ---- Voice ----
    voc = p.add_argument_group('Voice')
    voc.add_argument('--no-voice', action='store_true',
                     help='Disable voice conversion entirely')
    voc.add_argument('--voice-in', default=None,
                     help='Microphone device name or index (default: system default)')
    voc.add_argument('--voice-out', default=None,
                     help='Speaker / virtual cable device name or index')
    voc.add_argument('--rvc-api', default='',
                     help='Applio / RVC WebUI API URL (e.g. http://localhost:7865)')
    voc.add_argument('--list-audio', action='store_true',
                     help='Print available audio devices and exit')

    # ---- Streaming output ----
    out = p.add_argument_group('Streaming output')
    out.add_argument('--virtual-cam', action='store_true',
                     help='Expose as virtual camera for OBS (requires pyvirtualcam)')
    out.add_argument('--rtmp', default='',
                     help='Stream to RTMP URL, e.g. rtmp://live.twitch.tv/app/<key>')

    return p.parse_args()


def main():
    args = parse_args()

    if args.list_audio:
        VoiceEngine.list_devices()
        return

    print("=" * 62)
    print("  AI Influencer Live Stream Overlay")
    print("=" * 62)
    if Path(args.creators_dir).is_dir():
        print(f"  Creators dir : {args.creators_dir}  (N/P/1-9 to switch)")
    else:
        print(f"  Avatar       : {args.avatar}")
    if args.avatar_dir:
        print(f"  Avatar dir   : {args.avatar_dir}")
    print(f"  Camera       : {args.camera}  "
          f"({args.width}x{args.height} @ {args.fps} fps)")
    print(f"  Mode         : {args.mode}")
    print(f"  Opacity      : {args.opacity:.0%}")
    print(f"  Voice        : {'OFF' if args.no_voice else 'ON'}")
    if args.rvc_api:
        print(f"  RVC API      : {args.rvc_api}")
    if args.virtual_cam:
        print("  Virtual cam  : ENABLED")
    if args.rtmp:
        print(f"  RTMP         : {args.rtmp}")
    print("=" * 62)

    stream = AIInfluencerStream(
        # Visual
        avatar_path=args.avatar,
        avatar_dir=args.avatar_dir,
        creators_dir=args.creators_dir,
        # Camera
        camera_id=args.camera,
        width=args.width,
        height=args.height,
        fps=args.fps,
        flip_camera=not args.no_flip,
        # Overlay
        mode=args.mode,
        opacity=args.opacity,
        avatar_scale=args.scale,
        transition_frames=args.transition,
        # Streaming output
        use_virtual_cam=args.virtual_cam,
        rtmp_url=args.rtmp,
        # Voice
        voice_enabled=not args.no_voice,
        voice_input_device=args.voice_in,
        voice_output_device=args.voice_out,
        rvc_api_url=args.rvc_api,
    )
    stream.run()


if __name__ == '__main__':
    main()
