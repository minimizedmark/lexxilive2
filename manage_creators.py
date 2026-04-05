#!/usr/bin/env python3
"""
Creator management helper.

Commands:
  python manage_creators.py list
  python manage_creators.py add <slug> [display name]
  python manage_creators.py generate <slug> [--style "..."]
  python manage_creators.py info <slug>
  python manage_creators.py devices          ← list audio devices

Examples:
  python manage_creators.py list
  python manage_creators.py add lexi "Lexi"
  python manage_creators.py generate lexi --style "cyberpunk streamer"
  python manage_creators.py info lexi
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.creator import discover_creators, scaffold_creator, load_creator
from src.voice import VoiceEngine

CREATORS_DIR = Path('creators')


def cmd_list(args):
    creators = discover_creators(CREATORS_DIR)
    if not creators:
        print("No creator profiles found.")
        print(f"Add a creator with:  python manage_creators.py add <slug>")
        return
    print(f"\n{'Slot':<5} {'Slug':<18} {'Name':<20} {'Voice':<6} {'Pitch':>6}")
    print('-' * 60)
    for i, c in enumerate(creators, 1):
        voice = 'YES' if c.has_voice else 'no'
        print(f"  {i:<4} {c.slug:<18} {c.name:<20} {voice:<6} {c.pitch_shift:+d}")
    print()


def cmd_add(args):
    slug = args.slug.lower().replace(' ', '_')
    name = args.name or slug.title()
    CREATORS_DIR.mkdir(exist_ok=True)
    d = scaffold_creator(CREATORS_DIR, slug, name)
    print(f"\nCreated:  {d}")
    print(f"\nNext steps:")
    print(f"  1. Copy your RVC model:  cp your_model.pth {d}/voice.pth")
    print(f"               (optional)  cp your_model.index {d}/voice.index")
    print(f"  2. Add an avatar:         cp your_avatar.png {d}/avatar.png")
    print(f"  3. Edit config:           {d}/config.json")
    print(f"        pitch_shift: number of semitones to shift voice up (+) or down (-)")
    print(f"  4. Generate avatar with AI: python manage_creators.py generate {slug}")


def cmd_generate(args):
    """Generate an AI avatar for this creator using generate_avatar.py logic."""
    slug = args.slug
    creator_dir = CREATORS_DIR / slug
    if not creator_dir.exists():
        print(f"Creator '{slug}' not found. Run:  python manage_creators.py add {slug}")
        sys.exit(1)

    cfg_file = creator_dir / 'config.json'
    cfg = json.loads(cfg_file.read_text()) if cfg_file.exists() else {}
    name = cfg.get('name', slug.title())
    style = args.style or cfg.get('style', '')

    import os
    if not os.environ.get('ANTHROPIC_API_KEY'):
        print("[Error] ANTHROPIC_API_KEY not set.")
        sys.exit(1)

    # Import generator logic inline
    sys.path.insert(0, str(Path(__file__).parent))
    import generate_avatar as ga

    print(f"[Generate] Building persona for: {name}")
    persona = ga.generate_persona(style=f'{name} creator – {style}' if style else name)

    print(f"[Generate] Generating image…")
    image_bytes = ga.generate_image(persona['image_prompt'])

    print(f"[Generate] Removing background…")
    image_bytes = ga.remove_background(image_bytes)

    out_path = creator_dir / 'avatar.png'
    out_path.write_bytes(image_bytes)
    print(f"\n[Done] Avatar saved: {out_path}")

    # Save persona info into config
    cfg.update({
        'name': cfg.get('name', persona.get('name', name)),
        'tagline': persona.get('tagline', ''),
        'description': persona.get('tagline', ''),
    })
    cfg_file.write_text(json.dumps(cfg, indent=2))
    print(f"       Config updated: {cfg_file}")


def cmd_info(args):
    slug = args.slug
    creator_dir = CREATORS_DIR / slug
    c = load_creator(creator_dir)
    if c is None:
        print(f"Creator '{slug}' not found or missing avatar.")
        return
    print(f"\n  Slug    : {c.slug}")
    print(f"  Name    : {c.name}")
    print(f"  Dir     : {c.directory}")
    print(f"  Avatar  : {c.avatar_path}")
    print(f"  Voice   : {c.voice_model_path or 'none'}")
    print(f"  Index   : {c.voice_index_path or 'none'}")
    print(f"  Pitch   : {c.pitch_shift:+d} semitones")
    print(f"  Desc    : {c.description or '(none)'}")
    print(f"  Tags    : {', '.join(c.tags) or '(none)'}\n")


def cmd_devices(_args):
    VoiceEngine.list_devices()


def main():
    p = argparse.ArgumentParser(description='Creator profile manager')
    sub = p.add_subparsers(dest='command')

    sub.add_parser('list', help='List all creator profiles')

    add_p = sub.add_parser('add', help='Scaffold a new creator directory')
    add_p.add_argument('slug', help='Short identifier, e.g. lexi')
    add_p.add_argument('name', nargs='?', default='',
                       help='Display name, e.g. "Lexi" (defaults to slug.title())')

    gen_p = sub.add_parser('generate', help='Generate an AI avatar for a creator')
    gen_p.add_argument('slug')
    gen_p.add_argument('--style', default='', help='Style hint for the avatar')

    info_p = sub.add_parser('info', help='Show details for a creator')
    info_p.add_argument('slug')

    sub.add_parser('devices', help='List audio input/output devices')

    args = p.parse_args()

    dispatch = {
        'list':     cmd_list,
        'add':      cmd_add,
        'generate': cmd_generate,
        'info':     cmd_info,
        'devices':  cmd_devices,
    }

    if args.command not in dispatch:
        p.print_help()
        return

    dispatch[args.command](args)


if __name__ == '__main__':
    main()
