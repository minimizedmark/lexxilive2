"""
Face detection and body segmentation using MediaPipe.
"""

import cv2
import numpy as np

try:
    import mediapipe as mp
    MEDIAPIPE_AVAILABLE = True
except ImportError:
    MEDIAPIPE_AVAILABLE = False


class FaceDetector:
    """Detects faces and estimates head tilt using MediaPipe or OpenCV fallback."""

    def __init__(self):
        self._mp_detector = None
        self._face_mesh = None
        self._cv_detector = None

        if MEDIAPIPE_AVAILABLE:
            try:
                mp_face = mp.solutions.face_detection
                self._mp_detector = mp_face.FaceDetection(
                    model_selection=1,
                    min_detection_confidence=0.5,
                )
            except Exception as e:
                print(f"MediaPipe face detector init failed: {e}")

            try:
                mp_mesh = mp.solutions.face_mesh
                self._face_mesh = mp_mesh.FaceMesh(
                    max_num_faces=1,
                    refine_landmarks=False,
                    min_detection_confidence=0.5,
                    min_tracking_confidence=0.5,
                )
            except Exception as e:
                print(f"MediaPipe face mesh init failed: {e}")

        if self._mp_detector is None:
            cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
            self._cv_detector = cv2.CascadeClassifier(cascade_path)
            print("Using OpenCV Haar cascade face detector (fallback).")

    def detect(self, frame):
        """
        Returns list of dicts: x, y, w, h, confidence, tilt_deg.
        tilt_deg is the clockwise roll angle of the head (0 = upright).
        """
        if self._mp_detector is not None:
            return self._detect_mediapipe(frame)
        return self._detect_opencv(frame)

    def _detect_mediapipe(self, frame):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self._mp_detector.process(rgb)
        faces = []
        if results.detections:
            h, w = frame.shape[:2]
            for det in results.detections:
                bb = det.location_data.relative_bounding_box
                x = int(bb.xmin * w)
                y = int(bb.ymin * h)
                fw = int(bb.width * w)
                fh = int(bb.height * h)
                faces.append({'x': x, 'y': y, 'w': fw, 'h': fh,
                               'confidence': float(det.score[0]),
                               'tilt_deg': 0.0})

        # Enrich with tilt from face mesh
        if faces and self._face_mesh is not None:
            tilt = self._estimate_tilt(rgb, frame.shape)
            for f in faces:
                f['tilt_deg'] = tilt

        return faces

    def _estimate_tilt(self, rgb, shape) -> float:
        """
        Use face mesh landmarks to estimate head roll (tilt) in degrees.
        Positive = tilted clockwise (right ear toward shoulder).
        """
        results = self._face_mesh.process(rgb)
        if not results.multi_face_landmarks:
            return 0.0
        landmarks = results.multi_face_landmarks[0].landmark
        h, w = shape[:2]
        # Left eye outer corner (index 33) and right eye outer corner (index 263)
        left  = landmarks[33]
        right = landmarks[263]
        lx, ly = left.x * w,  left.y * h
        rx, ry = right.x * w, right.y * h
        import math
        angle = math.degrees(math.atan2(ry - ly, rx - lx))
        return angle  # degrees; negative = tilted counter-clockwise

    def _detect_opencv(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        rects = self._cv_detector.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
        faces = []
        for (x, y, w, h) in rects:
            faces.append({'x': int(x), 'y': int(y), 'w': int(w), 'h': int(h),
                          'confidence': 1.0, 'tilt_deg': 0.0})
        return faces


class BodySegmenter:
    """Segments the person (selfie) from the background using MediaPipe."""

    def __init__(self):
        self._segmenter = None
        if MEDIAPIPE_AVAILABLE:
            try:
                mp_seg = mp.solutions.selfie_segmentation
                self._segmenter = mp_seg.SelfieSegmentation(model_selection=1)
            except Exception as e:
                print(f"MediaPipe segmentation init failed: {e}")

    @property
    def available(self):
        return self._segmenter is not None

    def get_mask(self, frame):
        """
        Returns a float32 mask (H x W) with values 0.0–1.0.
        1.0 = person, 0.0 = background.
        Returns None if segmentation is unavailable.
        """
        if self._segmenter is None:
            return None
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self._segmenter.process(rgb)
        mask = results.segmentation_mask  # float32 H x W
        # Smooth edges
        mask = cv2.GaussianBlur(mask, (15, 15), 0)
        return mask
