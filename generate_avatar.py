#!/usr/bin/env python3
"""
AI Influencer Avatar Generator
================================
Uses the Anthropic Claude API to design a unique AI influencer persona,
then generates a photorealistic avatar image via an image-generation backend.

Supported image backends (tried in order):
  1. Together AI  –  fast, cheap, good quality   (TOGETHER_API_KEY)
  2. Replicate    –  Stable Diffusion via API    (REPLICATE_API_TOKEN)
  3. OpenAI DALL-E 3                             (OPENAI_API_KEY)

After generating, the background is automatically removed (rembg) so the
avatar can be overlaid transparently on the live stream.

Usage
-----
  export ANTHROPIC_API_KEY=sk-ant-...
  export TOGETHER_API_KEY=...          # or REPLICATE_API_TOKEN / OPENAI_API_KEY
  python generate_avatar.py
  python generate_avatar.py --style "cyberpunk gamer"
  python generate_avatar.py --out assets/my_influencer.png
"""

import argparse
import base64
import io
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Persona generation via Claude
# ---------------------------------------------------------------------------

def generate_persona(style: str) -> dict:
    """Ask Claude to invent an AI influencer persona and return structured data."""
    try:
        import anthropic
    except ImportError:
        print("[Error] anthropic package not installed.  pip install anthropic")
        sys.exit(1)

    client = anthropic.Anthropic()

    prompt = f"""You are a creative director specialising in AI virtual influencers.
Design a unique, visually striking AI influencer persona.
{"Style inspiration: " + style if style else ""}

Return ONLY a JSON object (no markdown, no explanation) with these exact keys:
{{
  "name": "<influencer name>",
  "tagline": "<one-sentence tagline>",
  "image_prompt": "<detailed Stable Diffusion / DALL-E prompt for a portrait photo, ultra-realistic, studio lighting, plain transparent-friendly background, upper body shot>"
}}

The image_prompt must:
- Be 60-120 words
- Describe a specific, visually consistent character
- Mention: gender, hair, eye colour, clothing style, background (solid colour or gradient)
- Include quality tags: ultra-realistic, 8k, studio lighting, sharp focus, photorealistic
- NOT include watermarks, text, logos, or multiple people
"""

    message = client.messages.create(
        model='claude-opus-4-6',
        max_tokens=512,
        messages=[{'role': 'user', 'content': prompt}],
    )

    import json
    raw = message.content[0].text.strip()
    # Strip markdown fences if present
    if raw.startswith('```'):
        raw = raw.split('```')[1]
        if raw.startswith('json'):
            raw = raw[4:]
    persona = json.loads(raw)
    return persona


# ---------------------------------------------------------------------------
# Image generation backends
# ---------------------------------------------------------------------------

def generate_image_together(prompt: str) -> bytes:
    import requests
    api_key = os.environ['TOGETHER_API_KEY']
    resp = requests.post(
        'https://api.together.xyz/v1/images/generations',
        headers={'Authorization': f'Bearer {api_key}'},
        json={
            'model': 'black-forest-labs/FLUX.1-schnell-Free',
            'prompt': prompt,
            'width': 768,
            'height': 1024,
            'steps': 4,
            'n': 1,
        },
        timeout=120,
    )
    resp.raise_for_status()
    b64 = resp.json()['data'][0]['b64_json']
    return base64.b64decode(b64)


def generate_image_replicate(prompt: str) -> bytes:
    import replicate
    import requests
    output = replicate.run(
        'stability-ai/sdxl:39ed52f2a78e934b3ba6e2a89f5b1c712de7dfea535525255b1aa35c5565e08b',
        input={
            'prompt': prompt,
            'width': 768,
            'height': 1024,
            'num_outputs': 1,
        },
    )
    url = output[0]
    return requests.get(url, timeout=60).content


def generate_image_openai(prompt: str) -> bytes:
    import openai
    import requests
    client = openai.OpenAI()
    resp = client.images.generate(
        model='dall-e-3',
        prompt=prompt,
        size='1024x1024',
        quality='standard',
        response_format='b64_json',
    )
    b64 = resp.data[0].b64_json
    return base64.b64decode(b64)


def generate_image(prompt: str) -> bytes:
    """Try each backend in order until one succeeds."""
    backends = []
    if os.environ.get('TOGETHER_API_KEY'):
        backends.append(('Together AI (FLUX)', generate_image_together))
    if os.environ.get('REPLICATE_API_TOKEN'):
        backends.append(('Replicate (SDXL)', generate_image_replicate))
    if os.environ.get('OPENAI_API_KEY'):
        backends.append(('OpenAI DALL-E 3', generate_image_openai))

    if not backends:
        print("\n[Error] No image generation API key found.")
        print("Set one of:")
        print("  TOGETHER_API_KEY    – https://api.together.xyz")
        print("  REPLICATE_API_TOKEN – https://replicate.com")
        print("  OPENAI_API_KEY      – https://platform.openai.com")
        sys.exit(1)

    for name, fn in backends:
        try:
            print(f"[Image] Using backend: {name}")
            return fn(prompt)
        except Exception as e:
            print(f"[Image] {name} failed: {e}")

    print("[Error] All image backends failed.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Background removal
# ---------------------------------------------------------------------------

def remove_background(image_bytes: bytes) -> bytes:
    """Remove background using rembg. Returns PNG bytes with alpha channel."""
    try:
        from rembg import remove
        output = remove(image_bytes)
        return output
    except ImportError:
        print("[Warning] rembg not installed – background will NOT be removed.")
        print("          pip install rembg onnxruntime")
        return image_bytes


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description='Generate an AI Influencer Avatar')
    p.add_argument('--style', default='',
                   help='Style hint, e.g. "cyberpunk gamer", "cottagecore", "luxury fashion"')
    p.add_argument('--out', default='assets/avatar.png',
                   help='Output PNG path (default: assets/avatar.png)')
    p.add_argument('--no-remove-bg', action='store_true',
                   help='Skip background removal')
    p.add_argument('--prompt', default='',
                   help='Provide your own image prompt instead of generating one via Claude')
    args = p.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Step 1 – persona / prompt
    if args.prompt:
        persona = {'name': 'Custom Avatar', 'tagline': '', 'image_prompt': args.prompt}
        print(f"[Persona] Using provided prompt.")
    else:
        if not os.environ.get('ANTHROPIC_API_KEY'):
            print("[Error] ANTHROPIC_API_KEY not set.")
            print("        export ANTHROPIC_API_KEY=sk-ant-...")
            sys.exit(1)
        print("[Persona] Generating AI influencer persona via Claude…")
        persona = generate_persona(args.style)
        print(f"\n  Name   : {persona['name']}")
        print(f"  Tagline: {persona['tagline']}")
        print(f"  Prompt : {persona['image_prompt'][:80]}…\n")

    # Step 2 – generate image
    print("[Image] Generating avatar image…")
    image_bytes = generate_image(persona['image_prompt'])

    # Step 3 – remove background
    if not args.no_remove_bg:
        print("[Image] Removing background…")
        image_bytes = remove_background(image_bytes)

    # Step 4 – save
    out_path.write_bytes(image_bytes)
    print(f"\n[Done] Avatar saved to: {out_path}")
    print(f"       Run  python main.py  to launch the stream overlay.")

    # Save persona metadata alongside
    import json
    meta_path = out_path.with_suffix('.json')
    meta_path.write_text(json.dumps(persona, indent=2))
    print(f"       Persona metadata: {meta_path}")


if __name__ == '__main__':
    main()
