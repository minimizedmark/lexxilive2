"""
Core stream overlay engine.
Captures webcam, composites the AI influencer avatar, and outputs to window
and/or virtual camera and/or RTMP stream.
"""

import cv2
import numpy as np
import time
from pathlib import Path

from .detector import FaceDetector, BodySegmenter
from .avatar import AvatarDeck
from .compositor import Compositor


MODES = ['face', 'replace', 'overlay', 'pip']

MODE_DESCRIPTIONS = {
    'face':    'FACE TRACK  – avatar follows your face',
    'replace': 'BODY REPLACE – avatar covers your body silhouette',
    'overlay': 'FULL OVERLAY – avatar fills the entire frame',
    'pip':     'PICTURE-IN-PIC – avatar in corner, real cam behind',
}


class AIInfluencerStream:
    """
    Main processing class.  Call run() to start the event loop.

    Keyboard controls (window must be focused):
      Q / ESC      Quit
      M            Cycle overlay mode
      N / →        Next avatar
      P / ←        Previous avatar
      1-9          Jump to avatar by position
      +  /  =      Increase opacity
      -             Decrease opacity
      R             Reload current avatar from disk
      S             Save screenshot
      H             Toggle help overlay
    """

    def __init__(
        self,
        avatar_path: str = 'assets/avatar.png',
        avatar_dir: str = '',
        camera_id: int = 0,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        mode: str = 'face',
        opacity: float = 0.92,
        use_virtual_cam: bool = False,
        rtmp_url: str = '',
        avatar_scale: float = 2.6,
        flip_camera: bool = True,
        transition_frames: int = 12,
    ):
        self.width = width
        self.height = height
        self.fps = fps
        self.mode = mode if mode in MODES else 'face'
        self.opacity = float(np.clip(opacity, 0.0, 1.0))
        self.use_virtual_cam = use_virtual_cam
        self.rtmp_url = rtmp_url
        self.avatar_scale = avatar_scale
        self.flip_camera = flip_camera

        self.show_help = False
        self._frame_count = 0
        self._fps_display = 0
        self._fps_timer = time.time()

        # Toast notification (shown briefly when avatar switches)
        self._toast_msg: str = ''
        self._toast_until: float = 0.0

        # Smoothed face / body position (EMA)
        self._smooth_x: float | None = None
        self._smooth_y: float | None = None
        self._smooth_w: float | None = None
        self._smooth_h: float | None = None
        self._smooth_tilt: float = 0.0
        # 0.6 = tight/responsive; lower = smoother but laggier
        self._smooth_alpha = 0.60

        print("[Stream] Initialising camera…")
        self.cap = cv2.VideoCapture(camera_id)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)

        print("[Stream] Loading avatar deck…")
        self.deck = AvatarDeck(
            avatar_path=avatar_path,
            avatar_dir=avatar_dir,
            transition_frames=transition_frames,
        )

        print("[Stream] Initialising face detector…")
        self.face_detector = FaceDetector()

        print("[Stream] Initialising body segmenter…")
        self.body_segmenter = BodySegmenter()

        self.compositor = Compositor()

        self.virtual_cam = None
        if use_virtual_cam:
            self._open_virtual_cam()

        self.writer = None
        if rtmp_url:
            self._open_rtmp(rtmp_url)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self):
        cv2.namedWindow('AI Influencer Stream', cv2.WINDOW_NORMAL)
        cv2.resizeWindow('AI Influencer Stream', self.width, self.height)

        print("[Stream] Running.  Press H for help, Q to quit.")

        while True:
            # Rescan avatar directory for new files
            self.deck.scan()

            ret, frame = self.cap.read()
            if not ret:
                print("[Stream] Camera read failed – retrying…")
                time.sleep(0.05)
                continue

            frame = cv2.resize(frame, (self.width, self.height))
            if self.flip_camera:
                frame = cv2.flip(frame, 1)

            output = self._process_frame(frame)
            self._update_fps()
            output = self._draw_ui(output)

            cv2.imshow('AI Influencer Stream', output)

            if self.virtual_cam is not None:
                self._send_virtual(output)

            if self.writer is not None:
                self.writer.write(output)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord('q'), 27):
                break
            self._handle_key(key)

        self._cleanup()

    # ------------------------------------------------------------------
    # Frame processing
    # ------------------------------------------------------------------

    def _process_frame(self, frame: np.ndarray) -> np.ndarray:
        if self.mode == 'face':
            return self._mode_face_track(frame)
        elif self.mode == 'replace':
            return self._mode_body_replace(frame)
        elif self.mode == 'overlay':
            return self._mode_full_overlay(frame)
        elif self.mode == 'pip':
            return self._mode_pip(frame)
        return frame

    def _current_avatar(self, width: int, height: int) -> np.ndarray:
        """Get the current (possibly cross-fading) avatar at the given size."""
        return self.deck.get_frame(width, height)

    def _mode_face_track(self, frame: np.ndarray) -> np.ndarray:
        """Track face position, size, and head tilt in real time."""
        faces = self.face_detector.detect(frame)
        output = frame.copy()

        if not faces:
            if self._smooth_x is None:
                return output
        else:
            face = max(faces, key=lambda f: f['w'] * f['h'])
            fx = float(face['x'] + face['w'] / 2)
            fy = float(face['y'] + face['h'] / 2)
            fw = float(face['w'])
            fh = float(face['h'])
            tilt = float(face.get('tilt_deg', 0.0))

            a = self._smooth_alpha
            if self._smooth_x is None:
                self._smooth_x, self._smooth_y = fx, fy
                self._smooth_w, self._smooth_h = fw, fh
                self._smooth_tilt = tilt
            else:
                self._smooth_x = a * fx + (1 - a) * self._smooth_x
                self._smooth_y = a * fy + (1 - a) * self._smooth_y
                self._smooth_w = a * fw + (1 - a) * self._smooth_w
                self._smooth_h = a * fh + (1 - a) * self._smooth_h
                dt = tilt - self._smooth_tilt
                if dt > 90:
                    dt -= 180
                elif dt < -90:
                    dt += 180
                self._smooth_tilt = self._smooth_tilt + a * dt

        cx, cy, fw = self._smooth_x, self._smooth_y, self._smooth_w

        aw = int(fw * self.avatar_scale)
        ah = int(aw * self.deck.current.aspect_ratio)
        avatar_cx = int(cx)
        avatar_cy = int(cy - ah * 0.35 + ah / 2)

        avatar_img = self._current_avatar(aw, ah)
        return self.compositor.overlay_rgba_rotated(
            output, avatar_img,
            avatar_cx, avatar_cy,
            self._smooth_tilt,
            self.opacity,
        )

    def _mode_body_replace(self, frame: np.ndarray) -> np.ndarray:
        """Erase person via segmentation, place avatar at their position."""
        if not self.body_segmenter.available:
            self.mode = 'face'
            return self._mode_face_track(frame)

        mask = self.body_segmenter.get_mask(frame)
        if mask is None:
            return frame

        mask_u8 = (mask * 255).astype(np.uint8)
        contours, _ = cv2.findContours(
            mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        mask3 = np.stack([mask, mask, mask], axis=-1)
        background = (frame.astype(np.float32) * (1.0 - mask3)).astype(np.uint8)

        if not contours:
            return background

        x, y, w, h = cv2.boundingRect(max(contours, key=cv2.contourArea))

        a = self._smooth_alpha
        bx, by, bw, bh = float(x + w / 2), float(y + h / 2), float(w), float(h)

        if self._smooth_x is None:
            self._smooth_x, self._smooth_y = bx, by
            self._smooth_w, self._smooth_h = bw, bh
        else:
            self._smooth_x = a * bx + (1 - a) * self._smooth_x
            self._smooth_y = a * by + (1 - a) * self._smooth_y
            self._smooth_w = a * bw + (1 - a) * self._smooth_w
            self._smooth_h = a * bh + (1 - a) * self._smooth_h

        ah = int(self._smooth_h)
        aw = int(ah / self.deck.current.aspect_ratio)
        ax = int(self._smooth_x - aw / 2)
        ay = int(self._smooth_y - ah / 2)

        avatar_img = self._current_avatar(aw, ah)
        return self.compositor.overlay_rgba(background, avatar_img, ax, ay, self.opacity)

    def _mode_full_overlay(self, frame: np.ndarray) -> np.ndarray:
        avatar_img = self._current_avatar(self.width, self.height)
        return self.compositor.overlay_rgba(frame, avatar_img, 0, 0, self.opacity)

    def _mode_pip(self, frame: np.ndarray) -> np.ndarray:
        pip_w = self.width // 4
        pip_h = int(pip_w * self.deck.current.aspect_ratio)
        pip_x = self.width - pip_w - 16
        pip_y = 16
        avatar_img = self._current_avatar(pip_w, pip_h)
        return self.compositor.overlay_rgba(frame, avatar_img, pip_x, pip_y, self.opacity)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _draw_ui(self, frame: np.ndarray) -> np.ndarray:
        out = frame.copy()
        h, w = out.shape[:2]

        def text(msg, x, y, scale=0.65, color=(0, 255, 120), thickness=2):
            cv2.putText(out, msg, (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                        scale, (0, 0, 0), thickness + 2, cv2.LINE_AA)
            cv2.putText(out, msg, (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                        scale, color, thickness, cv2.LINE_AA)

        text(f'FPS: {self._fps_display}', 10, 30)
        text(MODE_DESCRIPTIONS[self.mode], 10, 60)
        text(f'Opacity: {self.opacity:.0%}', 10, 90)

        # Avatar indicator
        deck = self.deck
        avatar_label = (f'Avatar [{deck.index + 1}/{deck.count}]: '
                        f'{deck.current.name}')
        if deck.in_transition:
            avatar_label += '  ↔'
        text(avatar_label, 10, 120, color=(255, 200, 60))

        text('[H] help  [N/P] avatar  [M] mode  [Q] quit', 10, h - 14,
             scale=0.5, color=(200, 200, 200))

        # Toast notification
        if time.time() < self._toast_until:
            tw, th = cv2.getTextSize(
                self._toast_msg, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)[0]
            tx = (w - tw) // 2
            ty = h // 2
            cv2.rectangle(out, (tx - 14, ty - th - 10),
                          (tx + tw + 14, ty + 10), (20, 20, 20), -1)
            text(self._toast_msg, tx, ty, scale=0.9,
                 color=(255, 240, 80), thickness=2)

        if self.show_help:
            lines = [
                'KEYBOARD CONTROLS',
                'Q / ESC   – Quit',
                'M         – Cycle overlay mode',
                'N / →     – Next avatar',
                'P / ←     – Previous avatar',
                '1 – 9     – Jump to avatar #',
                '+  /  =   – Increase opacity',
                '-         – Decrease opacity',
                'R         – Reload current avatar',
                'S         – Save screenshot',
                'H         – Toggle this help',
            ]
            pw = 360
            panel_x = w - pw - 10
            panel_y = 20
            cv2.rectangle(out, (panel_x - 10, panel_y - 10),
                          (w - 10, panel_y + len(lines) * 28 + 10),
                          (20, 20, 20), -1)
            for i, line in enumerate(lines):
                color = (255, 220, 60) if i == 0 else (220, 220, 220)
                text(line, panel_x, panel_y + i * 28 + 20,
                     scale=0.58, color=color, thickness=1)

        return out

    # ------------------------------------------------------------------
    # Key handling
    # ------------------------------------------------------------------

    def _handle_key(self, key: int):
        if key == ord('m'):
            idx = MODES.index(self.mode)
            self.mode = MODES[(idx + 1) % len(MODES)]
            # Reset smoothing state when mode changes
            self._smooth_x = self._smooth_y = None
            self._smooth_w = self._smooth_h = None
            print(f"[Stream] Mode → {self.mode}")

        elif key in (ord('n'), 0x27):       # N or right-arrow
            self.deck.next()
            self._toast(f'{self.deck.current.name}  [{self.deck.index + 1}/{self.deck.count}]')

        elif key in (ord('p'), 0x25):       # P or left-arrow
            self.deck.prev()
            self._toast(f'{self.deck.current.name}  [{self.deck.index + 1}/{self.deck.count}]')

        elif ord('1') <= key <= ord('9'):
            slot = key - ord('1')           # 0-based
            self.deck.select(slot)
            self._toast(f'{self.deck.current.name}  [{self.deck.index + 1}/{self.deck.count}]')

        elif key in (ord('+'), ord('=')):
            self.opacity = min(1.0, self.opacity + 0.05)

        elif key == ord('-'):
            self.opacity = max(0.05, self.opacity - 0.05)

        elif key == ord('r'):
            self.deck.reload_current()

        elif key == ord('s'):
            self._save_screenshot()

        elif key == ord('h'):
            self.show_help = not self.show_help

    def _toast(self, msg: str, duration: float = 1.8):
        self._toast_msg = msg
        self._toast_until = time.time() + duration

    # ------------------------------------------------------------------
    # Virtual camera / RTMP / screenshot
    # ------------------------------------------------------------------

    def _open_virtual_cam(self):
        try:
            import pyvirtualcam
            self.virtual_cam = pyvirtualcam.Camera(
                width=self.width, height=self.height, fps=self.fps,
                fmt=pyvirtualcam.PixelFormat.BGR,
            )
            print(f"[Stream] Virtual camera active: {self.virtual_cam.device}")
        except ImportError:
            print("[Stream] pyvirtualcam not installed – virtual camera disabled.")
            print("         pip install pyvirtualcam")
            print("         Linux also needs:  sudo modprobe v4l2loopback")
        except Exception as e:
            print(f"[Stream] Virtual camera error: {e}")

    def _send_virtual(self, frame: np.ndarray):
        try:
            self.virtual_cam.send(frame)
            self.virtual_cam.sleep_until_next_frame()
        except Exception as e:
            print(f"[Stream] Virtual cam send error: {e}")

    def _open_rtmp(self, url: str):
        fourcc = cv2.VideoWriter_fourcc(*'H264')
        self.writer = cv2.VideoWriter(
            url, fourcc, self.fps, (self.width, self.height))
        if not self.writer.isOpened():
            print(f"[Stream] WARNING: Could not open RTMP writer for {url}")
            self.writer = None
        else:
            print(f"[Stream] RTMP streaming to: {url}")

    def _save_screenshot(self):
        fname = f'screenshot_{int(time.time())}.png'
        ret, frame = self.cap.read()
        if ret:
            frame = cv2.resize(frame, (self.width, self.height))
            if self.flip_camera:
                frame = cv2.flip(frame, 1)
            out = self._process_frame(frame)
            out = self._draw_ui(out)
            cv2.imwrite(fname, out)
            print(f"[Stream] Screenshot saved: {fname}")

    # ------------------------------------------------------------------
    # FPS tracking / cleanup
    # ------------------------------------------------------------------

    def _update_fps(self):
        self._frame_count += 1
        now = time.time()
        if now - self._fps_timer >= 1.0:
            self._fps_display = self._frame_count
            self._frame_count = 0
            self._fps_timer = now

    def _cleanup(self):
        self.cap.release()
        cv2.destroyAllWindows()
        if self.virtual_cam is not None:
            try:
                self.virtual_cam.close()
            except Exception:
                pass
        if self.writer is not None:
            self.writer.release()
        print("[Stream] Stopped.")
