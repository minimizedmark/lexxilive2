"""
Avatar loading, deck management, and placeholder generation.

AvatarManager  – single avatar image with resize cache
AvatarDeck     – ordered list of avatars; supports live switching,
                 directory watching, and crossfade transitions
"""

import cv2
import numpy as np
import time
from pathlib import Path

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

SUPPORTED_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}


# ---------------------------------------------------------------------------
# Single-avatar manager
# ---------------------------------------------------------------------------

class AvatarManager:
    """
    Loads one PNG (preferably with alpha transparency) and serves
    resized copies on demand.  Keeps a small LRU resize cache.
    """

    MAX_CACHE = 30

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.name = self.path.stem          # display name (filename without ext)
        self._cache: dict = {}
        self._rgba: np.ndarray = self._load(self.path)
        h, w = self._rgba.shape[:2]
        self.aspect_ratio = h / w           # height / width

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_resized(self, width: int, height: int) -> np.ndarray:
        width, height = max(1, width), max(1, height)
        key = (width, height)
        if key not in self._cache:
            self._cache[key] = cv2.resize(
                self._rgba, (width, height),
                interpolation=cv2.INTER_LANCZOS4,
            )
            self._trim_cache()
        return self._cache[key]

    def reload(self) -> bool:
        """Re-read the file from disk. Returns True on success."""
        try:
            new = self._load(self.path)
            self._rgba = new
            self._cache.clear()
            h, w = self._rgba.shape[:2]
            self.aspect_ratio = h / w
            return True
        except Exception as e:
            print(f"[Avatar] Reload failed for '{self.path}': {e}")
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load(self, path: Path) -> np.ndarray:
        if not path.exists():
            print(f"[Avatar] '{path}' not found – using placeholder.")
            return _make_placeholder(str(path.stem))

        try:
            if PIL_AVAILABLE:
                img = Image.open(path).convert('RGBA')
                return np.array(img)
            else:
                bgr = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
                if bgr is None:
                    raise ValueError("cv2.imread returned None")
                if bgr.shape[2] == 3:
                    b, g, r = cv2.split(bgr)
                    a = np.full_like(b, 255)
                    bgra = cv2.merge([b, g, r, a])
                else:
                    bgra = bgr
                return bgra[:, :, [2, 1, 0, 3]]  # BGRA -> RGBA
        except Exception as e:
            print(f"[Avatar] Failed to load '{path}': {e} – using placeholder.")
            return _make_placeholder(str(path.stem))

    def _trim_cache(self):
        while len(self._cache) > self.MAX_CACHE:
            del self._cache[next(iter(self._cache))]


# ---------------------------------------------------------------------------
# Multi-avatar deck
# ---------------------------------------------------------------------------

class AvatarDeck:
    """
    Manages an ordered collection of avatars.

    Sources (combined, de-duplicated, sorted by name):
      - A single file path (--avatar)
      - A directory of images (--avatar-dir)

    Runtime operations:
      next()      / prev()      – cycle forward / backward
      select(idx)               – jump to avatar by index
      reload_current()          – re-read current avatar from disk
      scan()                    – rescan source directory for new/removed files
      crossfade_alpha           – 0.0 → 1.0 during transition; 1.0 = fully switched

    The deck automatically cross-fades between avatars over `transition_frames`
    rendered frames.
    """

    def __init__(
        self,
        avatar_path: str = '',
        avatar_dir: str = '',
        transition_frames: int = 12,
    ):
        self._dir: Path | None = None
        self._transition_frames = max(1, transition_frames)

        # Build initial list
        self._managers: list[AvatarManager] = []
        self._index: int = 0

        self._prev_manager: AvatarManager | None = None
        self._transition_progress: float = 1.0   # 1.0 = not transitioning

        self._dir_mtime: float = 0.0
        self._last_scan: float = 0.0

        paths: list[Path] = []

        if avatar_dir:
            d = Path(avatar_dir)
            if d.is_dir():
                self._dir = d
                paths += self._scan_dir(d)
            else:
                print(f"[Deck] avatar-dir '{avatar_dir}' is not a directory.")

        if avatar_path:
            p = Path(avatar_path)
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
                if p not in paths:
                    paths.append(p)
            elif not p.exists():
                # Will be a placeholder
                paths.append(p)

        # If nothing given, use default placeholder
        if not paths:
            paths.append(Path('assets/avatar.png'))

        # Deduplicate, keeping order
        seen: set = set()
        unique: list[Path] = []
        for p in paths:
            key = p.resolve()
            if key not in seen:
                seen.add(key)
                unique.append(p)

        for p in unique:
            self._managers.append(AvatarManager(p))

        print(f"[Deck] Loaded {len(self._managers)} avatar(s): "
              + ", ".join(m.name for m in self._managers))

    # ------------------------------------------------------------------
    # Current avatar
    # ------------------------------------------------------------------

    @property
    def current(self) -> AvatarManager:
        return self._managers[self._index]

    @property
    def count(self) -> int:
        return len(self._managers)

    @property
    def index(self) -> int:
        return self._index

    @property
    def names(self) -> list[str]:
        return [m.name for m in self._managers]

    # ------------------------------------------------------------------
    # Switching
    # ------------------------------------------------------------------

    def next(self):
        self._switch((self._index + 1) % self.count)

    def prev(self):
        self._switch((self._index - 1) % self.count)

    def select(self, idx: int):
        idx = idx % self.count
        if idx != self._index:
            self._switch(idx)

    def _switch(self, new_idx: int):
        if new_idx == self._index:
            return
        self._prev_manager = self._managers[self._index]
        self._index = new_idx
        self._transition_progress = 0.0
        print(f"[Deck] Switched to avatar [{self._index + 1}/{self.count}]: "
              f"{self.current.name}")

    # ------------------------------------------------------------------
    # Compositing with crossfade
    # ------------------------------------------------------------------

    def get_frame(self, width: int, height: int) -> np.ndarray:
        """
        Return the current avatar (RGBA) at the requested size,
        cross-faded with the previous avatar during a transition.
        """
        width, height = max(1, width), max(1, height)
        curr = self.current.get_resized(width, height)

        if self._transition_progress >= 1.0 or self._prev_manager is None:
            return curr

        # Advance transition
        self._transition_progress = min(
            1.0,
            self._transition_progress + 1.0 / self._transition_frames,
        )
        t = self._transition_progress          # 0 = prev, 1 = curr

        prev = self._prev_manager.get_resized(width, height)

        # Blend RGBA linearly
        blended = (prev.astype(np.float32) * (1.0 - t)
                   + curr.astype(np.float32) * t)
        return np.clip(blended, 0, 255).astype(np.uint8)

    @property
    def in_transition(self) -> bool:
        return self._transition_progress < 1.0

    # ------------------------------------------------------------------
    # Directory watching (call periodically)
    # ------------------------------------------------------------------

    def scan(self):
        """
        Rescan the avatar directory for new or removed files.
        Safe to call every frame; internally rate-limited to once per 2 s.
        """
        if self._dir is None:
            return
        now = time.time()
        if now - self._last_scan < 2.0:
            return
        self._last_scan = now

        try:
            mtime = self._dir.stat().st_mtime
        except OSError:
            return

        if mtime == self._dir_mtime:
            return
        self._dir_mtime = mtime

        new_paths = set(self._scan_dir(self._dir))
        existing = {m.path.resolve() for m in self._managers}

        added = [p for p in new_paths if p.resolve() not in existing]
        for p in sorted(added, key=lambda x: x.stem.lower()):
            self._managers.append(AvatarManager(p))
            print(f"[Deck] New avatar detected: {p.stem}")

        # Remove managers whose files have been deleted
        self._managers = [
            m for m in self._managers
            if not m.path.exists() is False  # keep placeholders
            or m.path.resolve() in new_paths
        ]

        # Clamp index
        if self._index >= len(self._managers):
            self._index = len(self._managers) - 1

    # ------------------------------------------------------------------
    # Reload
    # ------------------------------------------------------------------

    def reload_current(self) -> bool:
        ok = self.current.reload()
        print(f"[Deck] Reload '{self.current.name}': {'OK' if ok else 'FAILED'}")
        return ok

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    @staticmethod
    def _scan_dir(d: Path) -> list[Path]:
        return sorted(
            (p for p in d.iterdir() if p.suffix.lower() in SUPPORTED_EXTS),
            key=lambda p: p.stem.lower(),
        )


# ---------------------------------------------------------------------------
# Placeholder generator (standalone function, reused by AvatarManager)
# ---------------------------------------------------------------------------

def _make_placeholder(label: str = 'AI Influencer') -> np.ndarray:
    """Return a simple cartoon face as an RGBA numpy array."""
    W, H = 400, 560
    img = np.zeros((H, W, 4), dtype=np.uint8)

    skin     = (255, 210, 170, 235)
    hair     = (60, 35, 15, 255)
    shirt    = (80, 130, 220, 235)
    eye_col  = (40, 60, 160, 255)
    lip_col  = (210, 80, 100, 255)
    bg_glow  = (160, 90, 255, 60)

    cv2.ellipse(img, (W // 2, H // 2), (W // 2, H // 2), 0, 0, 360, bg_glow, -1)
    cv2.ellipse(img, (W // 2, H - 60), (int(W * 0.48), 120), 0, 180, 360, shirt, -1)
    cv2.rectangle(img, (W // 2 - 22, 260), (W // 2 + 22, 310), skin, -1)
    cv2.ellipse(img, (W // 2, 210), (115, 130), 0, 0, 360, skin, -1)
    cv2.ellipse(img, (W // 2, 180), (118, 105), 0, 180, 360, hair, -1)
    cv2.ellipse(img, (W // 2, 200), (125, 130), 0, 195, 345, hair, 18)

    for ex in (W // 2 - 38, W // 2 + 38):
        cv2.ellipse(img, (ex, 205), (22, 14), 0, 0, 360, (255, 255, 255, 255), -1)
        cv2.circle(img, (ex, 207), 11, eye_col, -1)
        cv2.circle(img, (ex, 207), 6, (10, 10, 10, 255), -1)
        cv2.circle(img, (ex - 4, 203), 4, (255, 255, 255, 200), -1)
        cv2.ellipse(img, (ex, 188), (20, 6), 0, 200, 340, hair, 3)

    cv2.ellipse(img, (W // 2, 230), (8, 12), 0, 0, 180, (220, 160, 130, 180), 2)
    cv2.ellipse(img, (W // 2, 254), (22, 8), 0, 0, 180, lip_col, -1)
    cv2.ellipse(img, (W // 2, 247), (22, 6), 0, 0, 180, (240, 120, 130, 200), -1)
    for ex in (W // 2 - 55, W // 2 + 55):
        cv2.ellipse(img, (ex, 232), (18, 10), 0, 0, 360, (255, 160, 160, 80), -1)

    short = label[:18]
    cv2.putText(img, short, (W // 2 - min(len(short) * 9, 160), H - 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (200, 100, 255, 255), 2, cv2.LINE_AA)
    return img
