"""
Frame compositing utilities.
"""

import cv2
import numpy as np


class Compositor:
    """Blends RGBA overlays onto BGR backgrounds."""

    def overlay_with_transform(self, background: np.ndarray,
                               overlay_rgba: np.ndarray,
                               cx: int, cy: int,
                               transform,          # animation.Transform
                               opacity: float = 1.0) -> np.ndarray:
        """
        Apply a full physics Transform (x-offset, y-offset, scale, rotation)
        then composite the avatar at (cx+dx, cy+dy).
        """
        if overlay_rgba is None:
            return background

        oh, ow = overlay_rgba.shape[:2]

        # Scale
        scale = float(transform.scale)
        if abs(scale - 1.0) > 0.001:
            new_w = max(1, int(ow * scale))
            new_h = max(1, int(oh * scale))
            overlay_rgba = cv2.resize(overlay_rgba, (new_w, new_h),
                                       interpolation=cv2.INTER_LINEAR)
            oh, ow = new_h, new_w

        # Rotation + translate
        final_cx = int(cx + transform.x)
        final_cy = int(cy + transform.y)

        return self.overlay_rgba_rotated(
            background, overlay_rgba,
            final_cx, final_cy,
            transform.rotation,
            opacity,
        )

    def overlay_rgba_rotated(self, background: np.ndarray, overlay_rgba: np.ndarray,
                              cx: int, cy: int, angle_deg: float,
                              opacity: float = 1.0) -> np.ndarray:
        """
        Rotate overlay_rgba by angle_deg (clockwise) around its centre,
        then composite it so its centre is at (cx, cy) on background.
        """
        if overlay_rgba is None or angle_deg == 0.0:
            oh, ow = overlay_rgba.shape[:2] if overlay_rgba is not None else (0, 0)
            return self.overlay_rgba(background, overlay_rgba,
                                     cx - ow // 2, cy - oh // 2, opacity)

        oh, ow = overlay_rgba.shape[:2]
        M = cv2.getRotationMatrix2D((ow / 2, oh / 2), -angle_deg, 1.0)
        rotated = cv2.warpAffine(
            overlay_rgba, M, (ow, oh),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0, 0),
        )
        return self.overlay_rgba(background, rotated,
                                 cx - ow // 2, cy - oh // 2, opacity)

    def overlay_rgba(self, background: np.ndarray, overlay_rgba: np.ndarray,
                     x: int, y: int, opacity: float = 1.0) -> np.ndarray:
        """
        Composite overlay_rgba (RGBA uint8) onto background (BGR uint8) at (x, y).
        Clips automatically if the overlay extends past the frame edges.
        Returns a new BGR frame.
        """
        if overlay_rgba is None or overlay_rgba.size == 0:
            return background

        bg = background.copy()
        bh, bw = bg.shape[:2]
        oh, ow = overlay_rgba.shape[:2]

        # Destination region on background
        x1b = max(0, x)
        y1b = max(0, y)
        x2b = min(bw, x + ow)
        y2b = min(bh, y + oh)

        if x2b <= x1b or y2b <= y1b:
            return bg  # Entirely outside

        # Corresponding source region on overlay
        x1o = x1b - x
        y1o = y1b - y
        x2o = x1o + (x2b - x1b)
        y2o = y1o + (y2b - y1b)

        src = overlay_rgba[y1o:y2o, x1o:x2o]

        if src.shape[2] == 4:
            alpha = src[:, :, 3:4].astype(np.float32) / 255.0 * opacity
            # Convert RGB -> BGR
            src_bgr = src[:, :, [2, 1, 0]].astype(np.float32)
        else:
            alpha = np.full((src.shape[0], src.shape[1], 1), opacity, dtype=np.float32)
            src_bgr = src.astype(np.float32)

        dst = bg[y1b:y2b, x1b:x2b].astype(np.float32)
        blended = dst * (1.0 - alpha) + src_bgr * alpha
        bg[y1b:y2b, x1b:x2b] = np.clip(blended, 0, 255).astype(np.uint8)
        return bg

    def blend_with_mask(self, background: np.ndarray, overlay_bgr: np.ndarray,
                        mask: np.ndarray, opacity: float = 1.0) -> np.ndarray:
        """
        Blend overlay_bgr into background using a float32 mask (0–1).
        mask should be H x W; applied scaled by opacity.
        """
        bg = background.copy().astype(np.float32)
        ov = overlay_bgr.astype(np.float32)
        alpha = np.clip(mask * opacity, 0.0, 1.0)
        if alpha.ndim == 2:
            alpha = alpha[:, :, np.newaxis]
        blended = bg * (1.0 - alpha) + ov * alpha
        return np.clip(blended, 0, 255).astype(np.uint8)
