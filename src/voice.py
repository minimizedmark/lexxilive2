"""
Real-time voice conversion engine.

Pipeline:
  Mic → capture thread → input queue → converter thread → output queue → playback thread → virtual speaker

Three backends (tried in order of preference):
  1. RVCLocalBackend  – uses infer-rvc-python for on-device inference
  2. RVCAPIBackend    – calls a running Applio / RVC WebUI HTTP server
  3. PassthroughBackend – routes mic directly to output (no conversion)

Creator voice models live in  creators/<name>/voice.pth  (+ optional .index).
"""

import threading
import queue
import time
import numpy as np
from pathlib import Path
from abc import ABC, abstractmethod

try:
    import sounddevice as sd
    SD_AVAILABLE = True
except ImportError:
    SD_AVAILABLE = False

try:
    import scipy.signal as signal
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False

CHUNK_DURATION = 0.35       # seconds per audio chunk fed to RVC
SAMPLE_RATE    = 40000      # RVC standard sample rate
CHANNELS       = 1
DTYPE          = 'float32'


# ---------------------------------------------------------------------------
# Converter backends
# ---------------------------------------------------------------------------

class VoiceBackend(ABC):
    """Common interface for all voice conversion backends."""

    name: str = 'base'

    @abstractmethod
    def load(self, model_path: Path, index_path: Path | None,
             pitch_shift: int) -> bool:
        """Load (or switch to) a creator voice model. Returns True on success."""

    @abstractmethod
    def convert(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Convert one chunk of float32 mono audio. Returns converted audio."""

    def unload(self):
        """Release model resources."""


class PassthroughBackend(VoiceBackend):
    """No conversion – routes audio unchanged. Always available."""

    name = 'passthrough'

    def load(self, model_path, index_path, pitch_shift):
        return True

    def convert(self, audio, sample_rate):
        return audio


class RVCLocalBackend(VoiceBackend):
    """
    Uses the  infer-rvc-python  pip package for local, GPU/CPU RVC inference.
    Install:  pip install infer-rvc-python
    """

    name = 'rvc-local'

    def __init__(self):
        self._rvc = None
        self._pitch_shift = 0

    def load(self, model_path: Path, index_path: Path | None,
             pitch_shift: int) -> bool:
        try:
            from rvc_python.infer import RVCInference
            self._rvc = RVCInference(
                models_path=str(model_path.parent),
                device='cuda:0' if self._cuda_available() else 'cpu',
            )
            self._rvc.load_model(
                model_path.name,
                index_path=str(index_path) if index_path and index_path.exists() else '',
            )
            self._pitch_shift = pitch_shift
            print(f"[Voice/local] Loaded: {model_path.name}")
            return True
        except ImportError:
            print("[Voice/local] infer-rvc-python not installed.")
            print("             pip install infer-rvc-python")
            return False
        except Exception as e:
            print(f"[Voice/local] Load failed: {e}")
            return False

    def convert(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        if self._rvc is None:
            return audio
        try:
            converted = self._rvc.infer_audio(
                audio,
                f0_up_key=self._pitch_shift,
                f0_method='rmvpe',
                index_rate=0.75,
                protect=0.33,
                filter_radius=3,
                resample_sr=sample_rate,
                rms_mix_rate=0.25,
            )
            return converted.astype(np.float32)
        except Exception as e:
            print(f"[Voice/local] Inference error: {e}")
            return audio

    def unload(self):
        self._rvc = None

    @staticmethod
    def _cuda_available() -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False


class RVCAPIBackend(VoiceBackend):
    """
    Sends audio to a running Applio or RVC WebUI server via its HTTP API.
    Default: http://localhost:7865  (standard RVC WebUI / Applio port)
    Set env var RVC_API_URL to override.
    """

    name = 'rvc-api'

    def __init__(self, api_url: str = ''):
        import os
        self._url = (api_url
                     or os.environ.get('RVC_API_URL', 'http://localhost:7865'))
        self._model_name = ''
        self._index_path = ''
        self._pitch_shift = 0

    def load(self, model_path: Path, index_path: Path | None,
             pitch_shift: int) -> bool:
        try:
            import requests
            resp = requests.get(f'{self._url}/api/health', timeout=3)
            resp.raise_for_status()
            self._model_name = model_path.stem
            self._index_path = str(index_path) if index_path else ''
            self._pitch_shift = pitch_shift
            print(f"[Voice/api] Connected to {self._url}, model: {model_path.name}")
            return True
        except Exception as e:
            print(f"[Voice/api] Cannot reach RVC server at {self._url}: {e}")
            return False

    def convert(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        try:
            import requests
            import io
            import soundfile as sf

            buf = io.BytesIO()
            sf.write(buf, audio, sample_rate, format='WAV')
            buf.seek(0)

            resp = requests.post(
                f'{self._url}/api/infer',
                data={
                    'model_name': self._model_name,
                    'f0_up_key': self._pitch_shift,
                    'index_path': self._index_path,
                    'index_rate': 0.75,
                    'protect': 0.33,
                    'f0_method': 'rmvpe',
                },
                files={'audio': ('input.wav', buf, 'audio/wav')},
                timeout=5,
            )
            resp.raise_for_status()

            out_buf = io.BytesIO(resp.content)
            converted, _ = sf.read(out_buf, dtype='float32')
            return converted
        except Exception as e:
            print(f"[Voice/api] Inference error: {e}")
            return audio


# ---------------------------------------------------------------------------
# Voice Engine
# ---------------------------------------------------------------------------

class VoiceEngine:
    """
    Manages microphone capture, voice conversion, and audio output.
    Switching creators is thread-safe and takes effect within one chunk (~350 ms).

    Usage:
        engine = VoiceEngine()
        engine.load('creators/lexi/voice.pth', 'creators/lexi/voice.index', pitch=0)
        engine.start()
        ...
        engine.load('creators/nova/voice.pth', ...)   # hot-swap
        ...
        engine.stop()
    """

    def __init__(
        self,
        input_device=None,
        output_device=None,
        sample_rate: int = SAMPLE_RATE,
        chunk_duration: float = CHUNK_DURATION,
        api_url: str = '',
    ):
        if not SD_AVAILABLE:
            print("[Voice] sounddevice not installed.  pip install sounddevice")
            print("        Voice conversion will be disabled.")

        self.sample_rate = sample_rate
        self.chunk_size = int(sample_rate * chunk_duration)
        self.input_device = input_device
        self.output_device = output_device

        self._backend: VoiceBackend = PassthroughBackend()
        self._backend_lock = threading.Lock()

        self._in_q: queue.Queue = queue.Queue(maxsize=10)
        self._out_q: queue.Queue = queue.Queue(maxsize=10)

        self._running = False
        self._capture_thread: threading.Thread | None = None
        self._convert_thread: threading.Thread | None = None
        self._playback_thread: threading.Thread | None = None

        self._current_creator: str = ''
        self.muted: bool = False

        # Pre-select best available backend
        self._api_url = api_url

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, model_path: str | Path, index_path: str | Path | None = None,
             pitch_shift: int = 0) -> bool:
        """
        Hot-swap the voice model.  Safe to call while running.
        Tries local RVC → API RVC → passthrough in that order.
        """
        model_path = Path(model_path)
        if index_path:
            index_path = Path(index_path)
            if not index_path.exists():
                index_path = None

        if not model_path.exists():
            print(f"[Voice] Model not found: {model_path}. Using passthrough.")
            with self._backend_lock:
                self._backend = PassthroughBackend()
            return False

        backends_to_try = [
            RVCLocalBackend(),
            RVCAPIBackend(api_url=self._api_url),
        ]

        for backend in backends_to_try:
            if backend.load(model_path, index_path, pitch_shift):
                with self._backend_lock:
                    old = self._backend
                    self._backend = backend
                    old.unload()
                self._current_creator = model_path.stem
                print(f"[Voice] Active backend: {backend.name}  |  model: {model_path.stem}")
                return True

        print("[Voice] All backends failed – using passthrough.")
        with self._backend_lock:
            self._backend = PassthroughBackend()
        return False

    def load_passthrough(self):
        """Disable voice conversion (mute / no creator loaded)."""
        with self._backend_lock:
            self._backend = PassthroughBackend()
        self._current_creator = ''
        print("[Voice] Passthrough (no conversion).")

    def start(self):
        if not SD_AVAILABLE:
            return
        if self._running:
            return
        self._running = True
        self._capture_thread  = threading.Thread(target=self._capture_loop,  daemon=True)
        self._convert_thread  = threading.Thread(target=self._convert_loop,  daemon=True)
        self._playback_thread = threading.Thread(target=self._playback_loop, daemon=True)
        self._capture_thread.start()
        self._convert_thread.start()
        self._playback_thread.start()
        print(f"[Voice] Started.  Input: {self.input_device or 'default'}  "
              f"Output: {self.output_device or 'default'}")

    def stop(self):
        self._running = False
        # Unblock queues
        for _ in range(4):
            try:
                self._in_q.put_nowait(None)
                self._out_q.put_nowait(None)
            except queue.Full:
                pass
        for t in (self._capture_thread, self._convert_thread, self._playback_thread):
            if t is not None:
                t.join(timeout=2)
        self._backend.unload()
        print("[Voice] Stopped.")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def current_creator(self) -> str:
        return self._current_creator

    # ------------------------------------------------------------------
    # Background threads
    # ------------------------------------------------------------------

    def _capture_loop(self):
        """Continuously read from the microphone and push chunks to _in_q."""
        while self._running:
            try:
                chunk = sd.rec(
                    self.chunk_size,
                    samplerate=self.sample_rate,
                    channels=CHANNELS,
                    dtype=DTYPE,
                    device=self.input_device,
                    blocking=True,
                )
                chunk = chunk.flatten()
                if not self.muted:
                    try:
                        self._in_q.put_nowait(chunk)
                    except queue.Full:
                        pass  # Drop oldest if pipeline is backed up
            except Exception as e:
                if self._running:
                    print(f"[Voice] Capture error: {e}")
                time.sleep(0.05)

    def _convert_loop(self):
        """Pull raw chunks, run voice conversion, push to _out_q."""
        while self._running:
            try:
                chunk = self._in_q.get(timeout=1.0)
            except queue.Empty:
                continue
            if chunk is None:
                break

            with self._backend_lock:
                backend = self._backend

            try:
                converted = backend.convert(chunk, self.sample_rate)
            except Exception as e:
                print(f"[Voice] Convert error: {e}")
                converted = chunk

            try:
                self._out_q.put_nowait(converted)
            except queue.Full:
                pass

    def _playback_loop(self):
        """Pull converted chunks and play them to the output device."""
        while self._running:
            try:
                chunk = self._out_q.get(timeout=1.0)
            except queue.Empty:
                continue
            if chunk is None:
                break

            try:
                sd.play(
                    chunk.reshape(-1, CHANNELS),
                    samplerate=self.sample_rate,
                    device=self.output_device,
                    blocking=True,
                )
            except Exception as e:
                if self._running:
                    print(f"[Voice] Playback error: {e}")
                time.sleep(0.02)

    # ------------------------------------------------------------------
    # Device helpers
    # ------------------------------------------------------------------

    @staticmethod
    def list_devices():
        if not SD_AVAILABLE:
            print("sounddevice not installed.")
            return
        print(sd.query_devices())

    @staticmethod
    def find_virtual_speaker() -> int | None:
        """Try to auto-detect a virtual audio output device."""
        if not SD_AVAILABLE:
            return None
        keywords = ['virtual', 'cable', 'vb-audio', 'loopback',
                    'blackhole', 'pulse', 'monitor']
        for i, dev in enumerate(sd.query_devices()):
            name = dev['name'].lower()
            if dev['max_output_channels'] > 0 and any(k in name for k in keywords):
                return i
        return None
