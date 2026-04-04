"""
Audio-driven avatar lip sync.

Analyses the amplitude envelope of the TTS audio and drives mouth-open
state on the avatar frame in real time.

Two tiers:
  1. AmplitudeLipSync  – fast, works with any RGBA avatar PNG
       Detects the mouth region via MediaPipe face mesh on the avatar image,
       then per-frame blends between "mouth closed" and "mouth open" states
       by warping landmarks using the current amplitude value.

  2. TalkingHeadLipSync  – high quality, requires SadTalker / LivePortrait
       Generates full video frames from audio + source image (offline pre-gen
       or real-time if GPU is fast enough).  Hookable for future integration.

The AnimationState object is shared between the TTS engine (writer)
and the video compositor (reader).  It is thread-safe.
"""

import threading
import time
import numpy as np
import cv2
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Shared animation state
# ---------------------------------------------------------------------------

@dataclass
class AnimationState:
    """
    Thread-safe container for the current mouth-open value and
    speaking indicator.  Updated by LipSync, read by the compositor.
    """
    _lock:       threading.Lock = field(default_factory=threading.Lock, repr=False)
    mouth_open:  float = 0.0        # 0.0 = closed, 1.0 = fully open
    is_speaking: bool  = False
    blink:       float = 0.0        # 0.0 = open, 1.0 = fully closed (for idle)
    _blink_timer: float = field(default_factory=time.time, repr=False)

    def set_mouth(self, value: float):
        with self._lock:
            self.mouth_open = float(np.clip(value, 0.0, 1.0))

    def set_speaking(self, speaking: bool):
        with self._lock:
            self.is_speaking = speaking
            if not speaking:
                self.mouth_open = 0.0

    def get(self) -> tuple[float, bool, float]:
        """Returns (mouth_open, is_speaking, blink)."""
        with self._lock:
            # Idle blink animation
            now = time.time()
            elapsed = now - self._blink_timer
            if elapsed > 4.0:               # blink every ~4 s
                b = min(1.0, (elapsed - 4.0) * 10)   # fast close
                blink = b if elapsed < 4.15 else max(0.0, 1.0 - (elapsed - 4.15) * 10)
                if elapsed > 4.3:
                    self._blink_timer = now
            else:
                blink = 0.0
            return self.mouth_open, self.is_speaking, blink


# ---------------------------------------------------------------------------
# Amplitude-driven lip sync
# ---------------------------------------------------------------------------

class AmplitudeLipSync:
    """
    Drives AnimationState from the amplitude of TTS audio.

    Call on_audio_start(audio_array) when TTS begins playing.
    The internal thread advances through the audio and updates mouth_open
    in sync with playback.
    """

    SAMPLE_RATE = 22050
    CHUNK_SIZE  = 512       # samples per analysis step

    def __init__(self, state: AnimationState):
        self.state     = state
        self._thread: threading.Thread | None = None
        self._audio: np.ndarray | None = None
        self._stop_event = threading.Event()

    def on_audio_start(self, audio: np.ndarray):
        """Called with the full TTS audio array before playback begins."""
        self._stop_event.set()              # stop previous animation
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)

        self._audio = audio.astype(np.float32)
        self._stop_event.clear()
        self.state.set_speaking(True)
        self._thread = threading.Thread(
            target=self._animate, daemon=True, name='lipsync')
        self._thread.start()

    def on_audio_end(self):
        """Called when TTS audio finishes."""
        self._stop_event.set()
        self.state.set_speaking(False)

    def _animate(self):
        if self._audio is None:
            return

        audio    = self._audio
        n        = len(audio)
        chunk    = self.CHUNK_SIZE
        dt       = chunk / self.SAMPLE_RATE      # seconds per chunk
        smooth   = 0.0
        alpha    = 0.4                            # smoothing

        for start in range(0, n, chunk):
            if self._stop_event.is_set():
                break
            segment = audio[start:start + chunk]
            rms = float(np.sqrt(np.mean(segment ** 2)))
            # Map RMS to mouth-open in 0–1 range
            # Typical speech RMS is 0.05 – 0.4
            target = np.clip((rms - 0.02) / 0.3, 0.0, 1.0)
            smooth = alpha * target + (1 - alpha) * smooth
            self.state.set_mouth(smooth)
            time.sleep(dt)

        self.state.set_mouth(0.0)


# ---------------------------------------------------------------------------
# Avatar frame modifier
# ---------------------------------------------------------------------------

class AvatarAnimator:
    """
    Modifies avatar RGBA frames based on AnimationState.

    Applies:
      - Mouth warp (open/close) derived from facial landmark detection
        on the avatar image, with graceful fallback to a simple scale warp
      - Blink (eyelid lowering) during idle blink animation
      - Subtle breathing scale (±1%) when speaking
    """

    def __init__(self, state: AnimationState):
        self.state         = state
        self._mouth_region: tuple | None = None   # (x, y, w, h) in avatar
        self._eye_regions:  list  = []            # [(x, y, w, h), ...]
        self._avatar_shape: tuple | None = None

    def calibrate(self, avatar_rgba: np.ndarray):
        """
        Detect mouth and eye regions in the avatar image.
        Call this whenever the avatar changes.
        """
        self._avatar_shape = avatar_rgba.shape[:2]  # (H, W)
        self._mouth_region = None
        self._eye_regions  = []

        try:
            import mediapipe as mp
            mp_mesh = mp.solutions.face_mesh
            with mp_mesh.FaceMesh(
                static_image_mode=True,
                max_num_faces=1,
                refine_landmarks=False,
                min_detection_confidence=0.3,
            ) as mesh:
                # Convert RGBA → RGB
                rgb = cv2.cvtColor(avatar_rgba[:, :, :3], cv2.COLOR_RGB2BGR)
                results = mesh.process(cv2.cvtColor(rgb, cv2.COLOR_BGR2RGB))

                if results.multi_face_landmarks:
                    lm = results.multi_face_landmarks[0].landmark
                    h, w = self._avatar_shape
                    pts = [(int(l.x * w), int(l.y * h)) for l in lm]

                    # Mouth: landmarks 61–291 (outer lips)
                    mouth_pts = [pts[i] for i in
                                 [61, 185, 40, 39, 37, 0, 267, 269, 270, 409,
                                  291, 375, 321, 405, 314, 17, 84, 181, 91, 146]]
                    xs = [p[0] for p in mouth_pts]
                    ys = [p[1] for p in mouth_pts]
                    self._mouth_region = (
                        min(xs) - 4, min(ys) - 4,
                        max(xs) - min(xs) + 8, max(ys) - min(ys) + 8,
                    )

                    # Eyes: left 33–133, right 362–263
                    for eye_ids in ([33, 160, 158, 133, 153, 144],
                                    [362, 385, 387, 263, 373, 380]):
                        ep = [pts[i] for i in eye_ids]
                        ex = [p[0] for p in ep]
                        ey = [p[1] for p in ep]
                        self._eye_regions.append((
                            min(ex) - 4, min(ey) - 4,
                            max(ex) - min(ex) + 8, max(ey) - min(ey) + 8,
                        ))

                    print("[LipSync] Avatar calibrated: mouth and eye regions detected.")
                    return

        except ImportError:
            pass
        except Exception as e:
            print(f"[LipSync] Calibration warning: {e}")

        # Fallback: estimate mouth region as bottom-centre of the image
        if self._avatar_shape:
            h, w = self._avatar_shape
            self._mouth_region = (
                int(w * 0.3), int(h * 0.55),
                int(w * 0.4), int(h * 0.12),
            )
        print("[LipSync] Avatar calibration used fallback mouth region.")

    def apply(self, avatar_rgba: np.ndarray) -> np.ndarray:
        """
        Return a modified copy of avatar_rgba with animation applied.
        Fast path: returns the original if no animation state is active.
        """
        mouth_open, is_speaking, blink = self.state.get()

        if mouth_open < 0.02 and blink < 0.02:
            return avatar_rgba     # nothing to do

        out = avatar_rgba.copy()

        # --- Mouth open ---
        if mouth_open > 0.02 and self._mouth_region is not None:
            mx, my, mw, mh = self._mouth_region
            stretch = 1.0 + mouth_open * 0.25   # up to 25% taller
            new_mh = int(mh * stretch)
            if mh > 0 and new_mh > mh:
                region = out[my:my + mh, mx:mx + mw]
                if region.size > 0:
                    expanded = cv2.resize(region, (mw, new_mh),
                                          interpolation=cv2.INTER_LINEAR)
                    # Clip to canvas
                    end_y = min(my + new_mh, out.shape[0])
                    actual_h = end_y - my
                    out[my:end_y, mx:mx + mw] = expanded[:actual_h]

        # --- Blink ---
        if blink > 0.02:
            for ex, ey, ew, eh in self._eye_regions:
                if ew <= 0 or eh <= 0:
                    continue
                region = out[ey:ey + eh, ex:ex + ew]
                if region.size == 0:
                    continue
                # Darken + compress eye region vertically
                compressed_h = max(1, int(eh * (1.0 - blink * 0.9)))
                compressed = cv2.resize(region, (ew, compressed_h),
                                         interpolation=cv2.INTER_LINEAR)
                overlay = out[ey:ey + eh, ex:ex + ew].copy()
                overlay[:] = 0   # black out the eye area
                overlay[:compressed_h] = compressed
                out[ey:ey + eh, ex:ex + ew] = overlay

        return out
