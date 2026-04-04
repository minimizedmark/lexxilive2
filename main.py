#!/usr/bin/env python3
"""
AI Influencer Live Stream Overlay
==================================
Captures your webcam and superimposes an AI-generated influencer avatar
over the live video.  Output goes to a preview window, and optionally to
a virtual camera (for OBS) or directly to an RTMP stream.

Usage
-----
  python main.py                          # defaults: face-track mode, camera 0
  python main.py --mode replace           # body-replacement mode
  python main.py --avatar my_avatar.png   # use your own AI-generated image
  python main.py --virtual-cam            # expose as a virtual camera in OBS
  python main.py --rtmp rtmp://live.twitch.tv/app/<key>

Generate an AI avatar first:
  python generate_avatar.py

Keyboard controls (in the preview window):
  Q / ESC   Quit
  M         Cycle overlay mode  (face → replace → overlay → pip)
  +  /  =   Increase opacity
  -          Decrease opacity
  R          Reload avatar image from disk (hot-swap)
  S          Save screenshot
  H          Toggle help overlay
"""

import argparse
import sys
from pathlib import Path

# Ensure src/ is importable when running directly
sys.path.insert(0, str(Path(__file__).parent))

from src.stream_overlay import AIInfluencerStream, MODES


def parse_args():
    p = argparse.ArgumentParser(
        description='AI Influencer Live Stream Overlay',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument('--avatar', default='assets/avatar.png',
                   help='Path to a single AI influencer PNG (with alpha transparency). '
                        'Default: assets/avatar.png')
    p.add_argument('--avatar-dir', default='',
                   help='Directory of avatar PNGs.  All images are loaded into a deck '
                        'and you can switch between them live with N/P/1-9 keys.  '
                        'The directory is watched for new files while running.')
    p.add_argument('--camera', type=int, default=0,
                   help='Camera device index (default: 0)')
    p.add_argument('--width', type=int, default=1280)
    p.add_argument('--height', type=int, default=720)
    p.add_argument('--fps', type=int, default=30)
    p.add_argument('--mode', default='face', choices=MODES,
                   help='Overlay mode (default: face)\n'
                        '  face    – avatar tracks your face\n'
                        '  replace – avatar replaces your body silhouette\n'
                        '  overlay – avatar fills the entire frame\n'
                        '  pip     – avatar in corner, full cam behind')
    p.add_argument('--opacity', type=float, default=0.92,
                   help='Avatar opacity 0.0–1.0 (default: 0.92)')
    p.add_argument('--scale', type=float, default=2.6,
                   help='Avatar scale relative to detected face (face mode, default: 2.6)')
    p.add_argument('--no-flip', action='store_true',
                   help='Disable horizontal mirror flip of camera input')
    p.add_argument('--virtual-cam', action='store_true',
                   help='Output to virtual camera device (requires pyvirtualcam + v4l2loopback)')
    p.add_argument('--rtmp', default='',
                   help='RTMP URL for direct streaming, e.g. rtmp://live.twitch.tv/app/<key>')
    p.add_argument('--transition', type=int, default=12,
                   help='Crossfade duration in frames when switching avatars (default: 12)')
    return p.parse_args()


def main():
    args = parse_args()

    print("=" * 60)
    print("  AI Influencer Live Stream Overlay")
    print("=" * 60)
    print(f"  Avatar : {args.avatar}")
    if args.avatar_dir:
        print(f"  Dir    : {args.avatar_dir}  (N/P/1-9 to switch)")
    print(f"  Camera : {args.camera}  ({args.width}x{args.height} @ {args.fps}fps)")
    print(f"  Mode   : {args.mode}")
    print(f"  Opacity: {args.opacity:.0%}")
    if args.virtual_cam:
        print("  Virtual camera: ENABLED")
    if args.rtmp:
        print(f"  RTMP stream  : {args.rtmp}")
    print("=" * 60)

    stream = AIInfluencerStream(
        avatar_path=args.avatar,
        avatar_dir=args.avatar_dir,
        camera_id=args.camera,
        width=args.width,
        height=args.height,
        fps=args.fps,
        mode=args.mode,
        opacity=args.opacity,
        use_virtual_cam=args.virtual_cam,
        rtmp_url=args.rtmp,
        avatar_scale=args.scale,
        flip_camera=not args.no_flip,
        transition_frames=args.transition,
    )
    stream.run()


if __name__ == '__main__':
    main()
