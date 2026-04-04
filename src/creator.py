"""
Creator profile – binds a visual avatar and a voice model under one identity.

Directory layout (one folder per creator):
  creators/
  └── lexi/
      ├── config.json          ← required: name, pitch_shift, description, tags
      ├── avatar.png           ← visual avatar (RGBA PNG)
      ├── voice.pth            ← RVC model weights
      └── voice.index          ← RVC feature index (optional, improves quality)

config.json minimal example:
  {
    "name": "Lexi",
    "pitch_shift": -3,
    "description": "Energetic gaming streamer with a bright, playful voice"
  }

config.json full example:
  {
    "name": "Lexi",
    "pitch_shift": -3,
    "description": "Energetic gaming streamer",
    "tags": ["gaming", "anime"],
    "avatar_file": "avatar.png",          <- override filename
    "voice_model_file": "voice.pth",      <- override filename
    "voice_index_file": "voice.index",    <- override filename
    "rvc_api_url": "http://localhost:7865" <- per-creator API endpoint
  }
"""

import json
from dataclasses import dataclass, field
from pathlib import Path


AVATAR_CANDIDATES  = ['avatar.png', 'avatar.jpg', 'avatar.jpeg', 'avatar.webp']
VOICE_CANDIDATES   = ['voice.pth', 'model.pth']
INDEX_CANDIDATES   = ['voice.index', 'model.index', 'added.index']


@dataclass
class Creator:
    slug: str                          # folder name (url-safe)
    name: str                          # display name
    directory: Path
    avatar_path: Path
    voice_model_path: Path | None
    voice_index_path: Path | None
    pitch_shift: int = 0
    description: str = ''
    tags: list[str] = field(default_factory=list)
    rvc_api_url: str = ''

    @property
    def has_voice(self) -> bool:
        return (self.voice_model_path is not None
                and self.voice_model_path.exists())

    def summary(self) -> str:
        voice_status = (f"voice: {self.voice_model_path.name}"
                        if self.has_voice else "voice: none (passthrough)")
        return (f"[{self.slug}] {self.name} | {voice_status} "
                f"| pitch: {self.pitch_shift:+d}")


def load_creator(directory: Path) -> Creator | None:
    """
    Load a Creator from a directory.  Returns None if the directory has
    no recognisable avatar or config.
    """
    if not directory.is_dir():
        return None

    slug = directory.name

    # --- config.json ---
    cfg: dict = {}
    cfg_file = directory / 'config.json'
    if cfg_file.exists():
        try:
            cfg = json.loads(cfg_file.read_text(encoding='utf-8'))
        except Exception as e:
            print(f"[Creator] Bad config.json in '{slug}': {e}")

    name = cfg.get('name', slug.title())
    pitch_shift = int(cfg.get('pitch_shift', 0))
    description = cfg.get('description', '')
    tags = cfg.get('tags', [])
    rvc_api_url = cfg.get('rvc_api_url', '')

    # --- avatar ---
    candidates = [cfg['avatar_file']] if 'avatar_file' in cfg else AVATAR_CANDIDATES
    avatar_path: Path | None = None
    for fn in candidates:
        p = directory / fn
        if p.exists():
            avatar_path = p
            break

    if avatar_path is None:
        # Accept any image in the folder
        for p in directory.iterdir():
            if p.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}:
                avatar_path = p
                break

    if avatar_path is None:
        print(f"[Creator] No avatar found in '{slug}' – skipping.")
        return None

    # --- voice model ---
    v_candidates = [cfg['voice_model_file']] if 'voice_model_file' in cfg else VOICE_CANDIDATES
    voice_model: Path | None = None
    for fn in v_candidates:
        p = directory / fn
        if p.exists():
            voice_model = p
            break
    if voice_model is None:
        # Accept any .pth in the folder
        for p in directory.iterdir():
            if p.suffix.lower() == '.pth':
                voice_model = p
                break

    # --- voice index ---
    i_candidates = [cfg['voice_index_file']] if 'voice_index_file' in cfg else INDEX_CANDIDATES
    voice_index: Path | None = None
    for fn in i_candidates:
        p = directory / fn
        if p.exists():
            voice_index = p
            break
    if voice_index is None:
        for p in directory.iterdir():
            if p.suffix.lower() == '.index':
                voice_index = p
                break

    return Creator(
        slug=slug,
        name=name,
        directory=directory,
        avatar_path=avatar_path,
        voice_model_path=voice_model,
        voice_index_path=voice_index,
        pitch_shift=pitch_shift,
        description=description,
        tags=tags,
        rvc_api_url=rvc_api_url,
    )


def discover_creators(creators_dir: str | Path) -> list[Creator]:
    """
    Scan a top-level directory for creator sub-folders.
    Returns a list of Creator objects sorted by slug.
    """
    base = Path(creators_dir)
    if not base.is_dir():
        return []

    creators: list[Creator] = []
    for sub in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if sub.is_dir() and not sub.name.startswith('.'):
            c = load_creator(sub)
            if c is not None:
                creators.append(c)
                print(f"[Creator] Found: {c.summary()}")

    return creators


def scaffold_creator(creators_dir: Path, slug: str, name: str = '') -> Path:
    """
    Create an empty creator directory with a starter config.json.
    Returns the new directory path.
    """
    d = creators_dir / slug
    d.mkdir(parents=True, exist_ok=True)
    cfg_file = d / 'config.json'
    if not cfg_file.exists():
        cfg = {
            "name": name or slug.title(),
            "pitch_shift": 0,
            "description": "AI influencer persona",
            "tags": []
        }
        cfg_file.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
        print(f"[Creator] Scaffolded: {d}")
        print(f"          Add avatar.png and voice.pth to {d}")
    return d
