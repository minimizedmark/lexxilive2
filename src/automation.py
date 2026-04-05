"""
Automation orchestrator – ties Brain, TTS, Chat, LipSync, and the
stream overlay together into a fully autonomous live stream.

In AUTO mode no human operator is required:
  - The AI reads chat and generates responses as the creator persona
  - Text-to-speech converts responses to audio
  - The avatar lip-syncs to the audio
  - The video compositor outputs to virtual camera / RTMP / preview window

In HYBRID mode the operator is present:
  - Voice conversion is active (mic → RVC → virtual speaker)
  - The AI Brain still watches chat and reacts to events
  - Operator can trigger AI speech manually (press space in preview window)

State machine:
  IDLE       → chat quiet, no speech scheduled
  LISTENING  → reading incoming chat / events
  THINKING   → waiting for Claude API response
  SPEAKING   → TTS audio playing, lip sync active
  REACTING   → high-priority event being processed (sub / raid / etc.)
"""

import threading
import queue
import time
import json
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from .brain     import Brain, CreatorPersona, StreamEvent, EventType, SpeakRequest
from .tts       import TTSEngine
from .lipsync   import AmplitudeLipSync, AvatarAnimator, AnimationState
from .creator   import Creator
from .animation import AnimationController, Reaction
from .reactions import ReactionEngine, StimulusType
from .hardware  import HardwareManager


class StreamState(Enum):
    IDLE      = auto()
    LISTENING = auto()
    THINKING  = auto()
    SPEAKING  = auto()
    REACTING  = auto()


class AutomationEngine:
    """
    Fully autonomous live stream engine.

    Wires together:
      Brain       – Claude-powered persona, decides what to say
      TTSEngine   – converts text to speech audio
      VoiceEngine – optional RVC voice conversion after TTS
      LipSync     – drives avatar mouth from audio amplitude
      Chat readers – Twitch / YouTube pushed to shared event queue

    The get_avatar_frame(base_frame) method is called by the video
    compositor each frame to apply lip sync and animation.
    """

    def __init__(
        self,
        creator: Creator,
        tts_engine: TTSEngine,
        voice_engine=None,          # src.voice.VoiceEngine (optional)
        mode: str = 'auto',         # 'auto' | 'hybrid'
        twitch_channel: str = '',
        youtube_video_id: str = '',
        youtube_channel_id: str = '',
        rvc_api_url: str = '',
    ):
        self.creator      = creator
        self.tts          = tts_engine
        self.voice_engine = voice_engine
        self.mode         = mode
        self.state        = StreamState.IDLE

        # Shared animation state (thread-safe, read by compositor each frame)
        self.anim_state   = AnimationState()
        self.lipsync      = AmplitudeLipSync(self.anim_state)
        self.animator     = AvatarAnimator(self.anim_state)

        # Physical animation + reaction engine
        self.anim_ctrl    = AnimationController()
        self.reactions    = ReactionEngine(
            self.anim_ctrl,
            on_state_change=self._on_emotion_change,
        )

        # Hardware (lights etc.) — drivers added externally via
        # engine.hardware.add(WLEDDriver(...))
        self.hardware     = HardwareManager()

        # Connect TTS callbacks to lip sync
        self.tts.on_start = self._on_tts_start
        self.tts.on_end   = self._on_tts_end

        # Load creator persona
        cfg = self._load_creator_cfg(creator)
        self.persona      = CreatorPersona(cfg)

        # Speak queue (receives SpeakRequest from Brain)
        self._speak_q: queue.PriorityQueue = queue.PriorityQueue()

        # Brain
        self.brain = Brain(
            persona=self.persona,
            on_speak=self._on_brain_speak,
        )

        # Chat event queue (shared between all chat readers)
        self.event_queue: queue.Queue[StreamEvent] = queue.Queue()

        # Chat readers (started lazily in start())
        self._chat_readers = []
        if twitch_channel:
            from .chat import TwitchChatReader
            self._chat_readers.append(
                TwitchChatReader(twitch_channel, self.event_queue))

        if youtube_video_id or youtube_channel_id:
            from .chat import YouTubeChatReader
            self._chat_readers.append(YouTubeChatReader(
                self.event_queue,
                live_video_id=youtube_video_id,
                channel_id=youtube_channel_id,
            ))

        self._running      = False
        self._dispatch_thread: threading.Thread | None = None
        self._speak_thread:    threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        self._running = True

        self.tts.start()
        self.brain.start()
        self.reactions.start()

        for reader in self._chat_readers:
            reader.start()

        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop, daemon=True, name='auto-dispatch')
        self._dispatch_thread.start()

        self._speak_thread = threading.Thread(
            target=self._speak_loop, daemon=True, name='auto-speak')
        self._speak_thread.start()

        self.event_queue.put_nowait(StreamEvent(type=EventType.STREAM_START))
        print(f"[Auto] Engine started.  Mode: {self.mode}  "
              f"Persona: {self.persona.name}")

    def stop(self):
        self._running = False
        self.event_queue.put_nowait(None)   # unblock dispatch
        self._speak_q.put_nowait((0, 0.0, None))

        for reader in self._chat_readers:
            reader.stop()

        self.brain.stop()
        self.tts.stop()
        self.reactions.stop()
        self.hardware.off_all()

        for t in (self._dispatch_thread, self._speak_thread):
            if t:
                t.join(timeout=3)

        print("[Auto] Engine stopped.")

    # ------------------------------------------------------------------
    # Per-frame call (used by stream_overlay compositor)
    # ------------------------------------------------------------------

    def get_avatar_frame(self, avatar_rgba, output_frame=None):
        """
        Apply lip sync, blink, expression, and spring-physics animation
        to the avatar RGBA frame each video frame.

        output_frame is the current BGR canvas — particles are drawn onto it.
        Returns (animated_avatar_rgba, transform) where transform carries the
        physics offset/scale/rotation for the compositor to apply.
        """
        import numpy as np

        # 1. Lip sync + blink from AmplitudeLipSync
        lip_animated = self.animator.apply(avatar_rgba)

        # 2. Expression + spring physics from AnimationController
        if output_frame is None:
            output_frame = np.zeros(
                (self.anim_ctrl.canvas_h, self.anim_ctrl.canvas_w, 3),
                dtype=np.uint8)

        final_avatar, transform = self.anim_ctrl.apply(lip_animated, output_frame)
        return final_avatar, transform

    def calibrate_avatar(self, avatar_rgba):
        """
        Detect mouth/eye regions in a new avatar image.
        Call this whenever the active avatar changes.
        """
        self.animator.calibrate(avatar_rgba)
        print(f"[Auto] Avatar calibrated for animation.")

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    @property
    def status_line(self) -> str:
        """One-line status string for the HUD."""
        icons = {
            StreamState.IDLE:      '○ IDLE',
            StreamState.LISTENING: '◉ LISTENING',
            StreamState.THINKING:  '⋯ THINKING',
            StreamState.SPEAKING:  '▶ SPEAKING',
            StreamState.REACTING:  '★ REACTING',
        }
        return f"AI [{icons.get(self.state, '?')}]  TTS:{self.tts.backend_name}"

    # ------------------------------------------------------------------
    # Internal: event dispatch
    # ------------------------------------------------------------------

    def _dispatch_loop(self):
        # Map stream event types to physical stimuli
        _event_stimulus = {
            EventType.RAID:         (StimulusType.RAID,        'raid',    1.2),
            EventType.SUBSCRIPTION: (StimulusType.SUBSCRIPTION,'sub',     1.0),
            EventType.GIFTED_SUB:   (StimulusType.GIFTED_SUB,  'gifted',  1.1),
            EventType.DONATION:     (StimulusType.DONATION,    'donation',1.0),
            EventType.BITS:         (StimulusType.BITS,        'bits',    0.8),
            EventType.FOLLOW:       (StimulusType.FOLLOW,      'follow',  0.6),
        }
        while self._running:
            try:
                event = self.event_queue.get(timeout=2.0)
            except queue.Empty:
                continue
            if event is None:
                break
            self.state = StreamState.LISTENING

            # Trigger physical reaction + hardware
            entry = _event_stimulus.get(event.type)
            if entry:
                stim, hw_tag, intensity = entry
                self.reactions.stimulate(stim, intensity=intensity)
                self.hardware.on_event(hw_tag)
            elif event.type == EventType.CHAT_MESSAGE:
                self.reactions.stimulate(StimulusType.FUNNY, intensity=0.3)

            self.brain.push_event(event)

    # ------------------------------------------------------------------
    # Internal: speak queue
    # ------------------------------------------------------------------

    def _on_brain_speak(self, req: SpeakRequest):
        """Called by Brain when it wants to say something."""
        # Parse emotion from the text before queuing
        self.reactions.set_emotion_from_text(req.text)
        # Talking reaction — subtle nod
        self.reactions.anim.trigger(Reaction.TALKING)
        self._speak_q.put_nowait((req.priority, time.time(), req.text))

    def _on_emotion_change(self, state):
        """Called by ReactionEngine when emotional label changes."""
        self.hardware.on_emotion(state)

    def _speak_loop(self):
        while self._running:
            try:
                priority, ts, text = self._speak_q.get(timeout=1.0)
            except queue.Empty:
                self.state = StreamState.IDLE
                continue
            if text is None:
                break

            # Wait if already speaking (avoid overlap)
            timeout = 30
            while self.tts.is_speaking and timeout > 0:
                time.sleep(0.1)
                timeout -= 1

            is_event = priority <= 3
            self.state = StreamState.REACTING if is_event else StreamState.THINKING
            self.brain.is_speaking = True

            self.tts.speak(text, priority=priority,
                           interrupt=(priority <= 2))

            self.brain.is_speaking = False

    # ------------------------------------------------------------------
    # TTS callbacks → lip sync
    # ------------------------------------------------------------------

    def _on_tts_start(self, audio):
        self.state = StreamState.SPEAKING
        self.lipsync.on_audio_start(audio)

    def _on_tts_end(self):
        self.lipsync.on_audio_end()
        self.state = StreamState.IDLE

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_creator_cfg(creator: Creator) -> dict:
        cfg_path = creator.directory / 'config.json'
        if cfg_path.exists():
            try:
                return json.loads(cfg_path.read_text(encoding='utf-8'))
            except Exception as e:
                print(f"[Auto] Could not parse config.json: {e}")
        return {'name': creator.name}


# ---------------------------------------------------------------------------
# Simple inject-only automation (no chat readers, for testing)
# ---------------------------------------------------------------------------

class ManualEventInjector:
    """
    Lets the operator manually inject events during a hybrid stream.
    Useful for testing the AI brain and TTS without a live chat feed.
    """

    def __init__(self, engine: AutomationEngine):
        self.engine = engine

    def chat(self, user: str, message: str):
        self.engine.event_queue.put_nowait(StreamEvent(
            type=EventType.CHAT_MESSAGE, user=user, message=message))

    def sub(self, user: str, months: int = 1):
        self.engine.event_queue.put_nowait(StreamEvent(
            type=EventType.SUBSCRIPTION, user=user,
            metadata={'months': months}))

    def raid(self, user: str, viewers: int = 50):
        self.engine.event_queue.put_nowait(StreamEvent(
            type=EventType.RAID, user=user, amount=viewers))

    def donation(self, user: str, amount: float, message: str = ''):
        self.engine.event_queue.put_nowait(StreamEvent(
            type=EventType.DONATION, user=user,
            amount=int(amount), message=message))
