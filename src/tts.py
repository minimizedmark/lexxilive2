"""
Text-to-Speech engine with optional RVC voice conversion.

Pipeline:
  text → [TTS backend] → raw audio → [RVC VoiceEngine] → output device

TTS backends (tried in order of preference):
  1. ElevenLabs  – best quality, voice cloning, streaming       (ELEVENLABS_API_KEY)
  2. Coqui XTTS  – open-source, local, voice cloning from samples
  3. pyttsx3     – system TTS fallback, no quality guarantees

After TTS generates audio, it is optionally passed through the RVC voice
engine (src/voice.py) to convert to the creator's signature voice.
This gives maximum flexibility:
  - ElevenLabs voice → RVC model  (best output, two-step)
  - XTTS cloned voice → direct    (single step, good quality)
  - system TTS → RVC model        (passable, lowest latency)
"""

import io
import threading
import queue
import time
import numpy as np
from abc import ABC, abstractmethod
from pathlib import Path

try:
    import sounddevice as sd
    SD_AVAILABLE = True
except ImportError:
    SD_AVAILABLE = False

try:
    import soundfile as sf
    SF_AVAILABLE = True
except ImportError:
    SF_AVAILABLE = False

SAMPLE_RATE = 22050


# ---------------------------------------------------------------------------
# TTS backends
# ---------------------------------------------------------------------------

class TTSBackend(ABC):
    name: str = 'base'

    @abstractmethod
    def synthesise(self, text: str, voice_id: str = '') -> np.ndarray | None:
        """Return float32 mono audio array at SAMPLE_RATE, or None on failure."""


class ElevenLabsBackend(TTSBackend):
    name = 'elevenlabs'

    def __init__(self, api_key: str = '', default_voice_id: str = ''):
        import os
        self._key = api_key or os.environ.get('ELEVENLABS_API_KEY', '')
        self._default_voice = default_voice_id
        if not self._key:
            raise RuntimeError("ELEVENLABS_API_KEY not set")

    def synthesise(self, text: str, voice_id: str = '') -> np.ndarray | None:
        try:
            import requests
            vid = voice_id or self._default_voice
            if not vid:
                # Use ElevenLabs' default "Rachel" voice
                vid = '21m00Tcm4TlvDq8ikWAM'

            resp = requests.post(
                f'https://api.elevenlabs.io/v1/text-to-speech/{vid}',
                headers={
                    'xi-api-key': self._key,
                    'Content-Type': 'application/json',
                    'Accept': 'audio/mpeg',
                },
                json={
                    'text': text,
                    'model_id': 'eleven_turbo_v2',
                    'voice_settings': {
                        'stability': 0.5,
                        'similarity_boost': 0.75,
                    },
                },
                timeout=15,
            )
            resp.raise_for_status()
            return self._mp3_to_array(resp.content)
        except Exception as e:
            print(f"[TTS/ElevenLabs] Error: {e}")
            return None

    @staticmethod
    def _mp3_to_array(mp3_bytes: bytes) -> np.ndarray | None:
        if not SF_AVAILABLE:
            return None
        try:
            buf = io.BytesIO(mp3_bytes)
            data, sr = sf.read(buf, dtype='float32')
            if data.ndim > 1:
                data = data.mean(axis=1)
            if sr != SAMPLE_RATE:
                import scipy.signal as sig
                data = sig.resample(data, int(len(data) * SAMPLE_RATE / sr))
            return data
        except Exception as e:
            print(f"[TTS] MP3 decode error: {e}")
            return None


class CoquiXTTSBackend(TTSBackend):
    """
    Coqui XTTS v2 – open-source, voice-clones from a reference audio file.
    voice_id should be the path to a 6–10 second reference WAV file.
    Install: pip install TTS
    """
    name = 'coqui-xtts'

    def __init__(self, reference_audio: str = '', language: str = 'en'):
        try:
            from TTS.api import TTS as CoquiTTS
            self._tts = CoquiTTS('tts_models/multilingual/multi-dataset/xtts_v2',
                                  gpu=self._gpu_available())
        except ImportError:
            raise RuntimeError("TTS package not installed. pip install TTS")
        self._ref_audio = reference_audio
        self._language  = language

    def synthesise(self, text: str, voice_id: str = '') -> np.ndarray | None:
        ref = voice_id or self._ref_audio
        if not ref or not Path(ref).exists():
            print("[TTS/Coqui] No reference audio – using default voice.")
            ref = None
        try:
            wav = self._tts.tts(
                text=text,
                speaker_wav=ref,
                language=self._language,
            )
            return np.array(wav, dtype=np.float32)
        except Exception as e:
            print(f"[TTS/Coqui] Error: {e}")
            return None

    @staticmethod
    def _gpu_available() -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False


class Pyttsx3Backend(TTSBackend):
    """System TTS fallback. Low quality but zero dependencies."""
    name = 'pyttsx3'

    def __init__(self):
        try:
            import pyttsx3
            self._engine = pyttsx3.init()
        except ImportError:
            raise RuntimeError("pyttsx3 not installed. pip install pyttsx3")

    def synthesise(self, text: str, voice_id: str = '') -> np.ndarray | None:
        if not SF_AVAILABLE or not SD_AVAILABLE:
            return None
        try:
            import tempfile, os
            tmp = tempfile.mktemp(suffix='.wav')
            self._engine.save_to_file(text, tmp)
            self._engine.runAndWait()
            data, sr = sf.read(tmp, dtype='float32')
            os.unlink(tmp)
            if data.ndim > 1:
                data = data.mean(axis=1)
            return data
        except Exception as e:
            print(f"[TTS/pyttsx3] Error: {e}")
            return None


# ---------------------------------------------------------------------------
# TTS Engine (orchestrator)
# ---------------------------------------------------------------------------

class TTSEngine:
    """
    High-level TTS engine.

    - Accepts text via speak() (non-blocking, queued)
    - Synthesises audio using the best available backend
    - Optionally passes through a VoiceEngine for RVC conversion
    - Plays audio to the configured output device
    - Provides on_start / on_end callbacks for lip sync

    Usage:
        engine = TTSEngine(voice_engine=my_voice_engine)
        engine.on_start = lambda audio: lipsync.start(audio)
        engine.on_end   = lambda: lipsync.stop()
        engine.start()
        engine.speak("Hello chat!")
        engine.stop()
    """

    def __init__(
        self,
        voice_id: str = '',
        output_device=None,
        sample_rate: int = SAMPLE_RATE,
        elevenlabs_api_key: str = '',
        coqui_reference_audio: str = '',
        language: str = 'en',
        voice_engine=None,          # src.voice.VoiceEngine or None
    ):
        self.voice_id      = voice_id
        self.output_device = output_device
        self.sample_rate   = sample_rate
        self.voice_engine  = voice_engine   # for RVC post-processing

        self.on_start: Callable | None = None    # called with audio array when speaking starts
        self.on_end:   Callable | None = None    # called when audio finishes

        self._q: queue.PriorityQueue = queue.PriorityQueue()
        self._running = False
        self._thread: threading.Thread | None = None
        self.is_speaking = False

        # Build backend chain
        self._backend = self._pick_backend(
            elevenlabs_api_key, coqui_reference_audio, language)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def speak(self, text: str, priority: int = 5, interrupt: bool = False):
        """Queue text for synthesis. Lower priority number = spoken sooner."""
        if interrupt:
            self._drain_queue()
        self._q.put((priority, time.time(), text))

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name='tts')
        self._thread.start()
        print(f"[TTS] Started. Backend: {self._backend.name}")

    def stop(self):
        self._running = False
        self._q.put((0, 0.0, None))    # sentinel
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def backend_name(self) -> str:
        return self._backend.name

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _loop(self):
        while self._running:
            try:
                priority, ts, text = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            if text is None:
                break
            self._synthesise_and_play(text, priority)

    def _synthesise_and_play(self, text: str, priority: int):
        audio = self._backend.synthesise(text, self.voice_id)
        if audio is None or len(audio) == 0:
            return

        # Optional: pass through RVC voice engine
        if self.voice_engine is not None:
            try:
                from .voice import RVCLocalBackend
                # Only post-process if a real model is loaded
                audio = self.voice_engine._backend.convert(audio, self.sample_rate)
            except Exception as e:
                print(f"[TTS] RVC post-process error: {e}")

        self.is_speaking = True
        if self.on_start:
            try:
                self.on_start(audio)
            except Exception:
                pass

        try:
            if SD_AVAILABLE:
                sd.play(audio.reshape(-1, 1), samplerate=self.sample_rate,
                        device=self.output_device, blocking=True)
            else:
                # Approximate duration sleep
                time.sleep(len(audio) / self.sample_rate)
        except Exception as e:
            print(f"[TTS] Playback error: {e}")
        finally:
            self.is_speaking = False
            if self.on_end:
                try:
                    self.on_end()
                except Exception:
                    pass

    def _drain_queue(self):
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    @staticmethod
    def _pick_backend(elevenlabs_key, coqui_ref, language) -> TTSBackend:
        import os
        key = elevenlabs_key or os.environ.get('ELEVENLABS_API_KEY', '')
        if key:
            try:
                b = ElevenLabsBackend(api_key=key)
                print("[TTS] Backend selected: ElevenLabs")
                return b
            except Exception as e:
                print(f"[TTS] ElevenLabs unavailable: {e}")

        if coqui_ref:
            try:
                b = CoquiXTTSBackend(reference_audio=coqui_ref, language=language)
                print("[TTS] Backend selected: Coqui XTTS")
                return b
            except Exception as e:
                print(f"[TTS] Coqui XTTS unavailable: {e}")

        try:
            b = Pyttsx3Backend()
            print("[TTS] Backend selected: pyttsx3 (fallback)")
            return b
        except Exception as e:
            print(f"[TTS] pyttsx3 unavailable: {e}")

        print("[TTS] WARNING: No TTS backend available. Audio will be silent.")

        class SilentBackend(TTSBackend):
            name = 'silent'
            def synthesise(self, text, voice_id=''):
                print(f"[TTS/silent] Would say: {text}")
                return np.zeros(int(SAMPLE_RATE * 0.5), dtype=np.float32)

        return SilentBackend()


# Callable type hint (avoid import cycle)
from typing import Callable
