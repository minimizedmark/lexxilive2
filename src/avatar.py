"""
Avatar loading, caching, and placeholder generation.
"""

import cv2
import numpy as np
from pathlib import Path

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


class AvatarManager:
    """
    Manages the AI influencer avatar image.
    Loads a PNG (with alpha transparency) and provides resized versions on demand.
    Falls back to a simple drawn placeholder if no image file is found.
    """

    MAX_CACHE = 30  # Max cached resolutions

    def __init__(self, avatar_path: str, canvas_width: int, canvas_height: int):
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self._cache: dict = {}
        self._avatar_rgba: np.ndarray | None = self._load(avatar_path)

        if self._avatar_rgba is not None:
            h, w = self._avatar_rgba.shape[:2]
            self.aspect_ratio = h / w  # height / width
        else:
            self.aspect_ratio = 4 / 3

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_resized(self, width: int, height: int) -> np.ndarray | None:
        """Return the avatar resized to (width, height), cached."""
        width = max(1, width)
        height = max(1, height)
        key = (width, height)
        if key not in self._cache:
            if self._avatar_rgba is None:
                return None
            resized = cv2.resize(
                self._avatar_rgba, (width, height),
                interpolation=cv2.INTER_LANCZOS4,
            )
            self._cache[key] = resized
            self._trim_cache()
        return self._cache[key]

    def reload(self, avatar_path: str) -> bool:
        """Hot-reload a new avatar image. Returns True on success."""
        new_img = self._load(avatar_path)
        if new_img is None:
            return False
        self._avatar_rgba = new_img
        self._cache.clear()
        h, w = self._avatar_rgba.shape[:2]
        self.aspect_ratio = h / w
        return True

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load(self, path: str) -> np.ndarray | None:
        p = Path(path)
        if not p.exists():
            print(f"[Avatar] No image found at '{path}'. Using placeholder.")
            print("         Run  python generate_avatar.py  to create a real AI avatar.")
            return self._make_placeholder()

        try:
            if PIL_AVAILABLE:
                img = Image.open(p).convert('RGBA')
                return np.array(img)
            else:
                # OpenCV path – no alpha; treat as opaque
                bgr = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
                if bgr is None:
                    raise ValueError("cv2.imread returned None")
                if bgr.shape[2] == 3:
                    bgr = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
                    bgr[:, :, 3] = 255
                # Convert BGRA -> RGBA
                rgba = bgr[:, :, [2, 1, 0, 3]]
                return rgba
        except Exception as e:
            print(f"[Avatar] Failed to load '{path}': {e}. Using placeholder.")
            return self._make_placeholder()

    @staticmethod
    def _make_placeholder() -> np.ndarray:
        """Draw a simple cartoon avatar as an RGBA image."""
        W, H = 400, 560
        img = np.zeros((H, W, 4), dtype=np.uint8)

        skin = (255, 210, 170, 235)
        hair = (60, 35, 15, 255)
        shirt = (80, 130, 220, 235)
        eye_col = (40, 60, 160, 255)
        lip_col = (210, 80, 100, 255)
        bg_glow = (160, 90, 255, 60)

        # Soft background glow
        cv2.ellipse(img, (W // 2, H // 2), (W // 2, H // 2), 0, 0, 360, bg_glow, -1)

        # Shoulders / top of body
        cv2.ellipse(img, (W // 2, H - 60), (int(W * 0.48), 120), 0, 180, 360, shirt, -1)

        # Neck
        cv2.rectangle(img, (W // 2 - 22, 260), (W // 2 + 22, 310), skin, -1)

        # Head
        cv2.ellipse(img, (W // 2, 210), (115, 130), 0, 0, 360, skin, -1)

        # Hair (top + sides)
        cv2.ellipse(img, (W // 2, 180), (118, 105), 0, 180, 360, hair, -1)
        cv2.ellipse(img, (W // 2, 200), (125, 130), 0, 195, 345, hair, 18)

        # Eyes (white + iris + pupil + highlight)
        for ex in (W // 2 - 38, W // 2 + 38):
            cv2.ellipse(img, (ex, 205), (22, 14), 0, 0, 360, (255, 255, 255, 255), -1)
            cv2.circle(img, (ex, 207), 11, eye_col, -1)
            cv2.circle(img, (ex, 207), 6, (10, 10, 10, 255), -1)
            cv2.circle(img, (ex - 4, 203), 4, (255, 255, 255, 200), -1)

        # Eyebrows
        for ex in (W // 2 - 38, W // 2 + 38):
            cv2.ellipse(img, (ex, 188), (20, 6), 0, 200, 340, hair, 3)

        # Nose
        cv2.ellipse(img, (W // 2, 230), (8, 12), 0, 0, 180, (220, 160, 130, 180), 2)

        # Lips
        cv2.ellipse(img, (W // 2, 254), (22, 8), 0, 0, 180, lip_col, -1)
        cv2.ellipse(img, (W // 2, 247), (22, 6), 0, 0, 180, (240, 120, 130, 200), -1)

        # Blush
        for ex in (W // 2 - 55, W // 2 + 55):
            cv2.ellipse(img, (ex, 232), (18, 10), 0, 0, 360, (255, 160, 160, 80), -1)

        # Label
        cv2.putText(img, 'AI Influencer', (W // 2 - 78, H - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (200, 100, 255, 255), 2,
                    cv2.LINE_AA)

        return img

    def _trim_cache(self):
        while len(self._cache) > self.MAX_CACHE:
            del self._cache[next(iter(self._cache))]
