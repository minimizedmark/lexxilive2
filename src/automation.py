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
import os
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional

from .brain     import Brain, CreatorPersona, StreamEvent, EventType, SpeakRequest
from .tts       import TTSEngine
from .lipsync   import AmplitudeLipSync, AvatarAnimator, AnimationState
from .creator   import Creator
from .animation import AnimationController, Reaction
from .reactions import ReactionEngine, StimulusType
from .hardware  import HardwareManager
from .supabase_bridge import SupabaseBridge


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
        bridge: Optional[SupabaseBridge] = None,
        api_url: str = '',
    ):
        self.creator      = creator
        self.tts          = tts_engine
        self.voice_engine = voice_engine
        self.mode         = mode
        self.state        = StreamState.IDLE

        # Supabase bridge — optional; connects to Node.js backend
        self._bridge:    Optional[SupabaseBridge] = bridge
        self._api_url:   str = api_url.rstrip('/')

        # Callback set by stream_overlay so bridge can trigger creator switches
        self.on_switch_creator: Optional[Callable[[str], None]] = None

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

        # Start Supabase bridge if configured
        if self._bridge is not None:
            self._bridge.on_command(self._handle_bridge_command)
            # Auto-create a session if we have an API URL and no session yet
            if self._api_url and not self._bridge.session_id:
                self._create_session()
            self._bridge.start()
            print("[Auto] Supabase bridge started.")

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

        if self._bridge is not None:
            # Mark session ended then close the WS
            if self._bridge.session_id and self._api_url:
                self._end_session()
            self._bridge.stop()

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
            self._set_state(StreamState.LISTENING)

            # Trigger physical reaction + hardware
            entry = _event_stimulus.get(event.type)
            if entry:
                stim, hw_tag, intensity = entry
                self.reactions.stimulate(stim, intensity=intensity)
                self.hardware.on_event(hw_tag)
            elif event.type == EventType.CHAT_MESSAGE:
                self.reactions.stimulate(StimulusType.FUNNY, intensity=0.3)

            # Report to dashboard
            self._report_event(event)

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
        # Push state snapshot to dashboard
        if self._bridge is not None:
            es = self.reactions.state
            self._bridge.report_state(
                state_label=self.state.name.lower(),
                emotion_valence=es.valence,
                arousal=es.arousal,
                creator_slug=self.creator.slug,
            )

    def _speak_loop(self):
        while self._running:
            try:
                priority, ts, text = self._speak_q.get(timeout=1.0)
            except queue.Empty:
                self._set_state(StreamState.IDLE)
                continue
            if text is None:
                break

            # Wait if already speaking (avoid overlap)
            timeout = 30
            while self.tts.is_speaking and timeout > 0:
                time.sleep(0.1)
                timeout -= 1

            is_event = priority <= 3
            self._set_state(StreamState.REACTING if is_event else StreamState.THINKING)
            self.brain.is_speaking = True

            self.tts.speak(text, priority=priority,
                           interrupt=(priority <= 2))

            self.brain.is_speaking = False

    # ------------------------------------------------------------------
    # TTS callbacks → lip sync
    # ------------------------------------------------------------------

    def _on_tts_start(self, audio):
        self._set_state(StreamState.SPEAKING)
        self.lipsync.on_audio_start(audio)

    def _on_tts_end(self):
        self.lipsync.on_audio_end()
        self._set_state(StreamState.IDLE)

    # ------------------------------------------------------------------
    # Bridge helpers
    # ------------------------------------------------------------------

    def _set_state(self, new_state: StreamState):
        """Update state and push a snapshot to the bridge."""
        self.state = new_state
        if self._bridge is not None:
            es = self.reactions.state
            self._bridge.report_state(
                state_label=new_state.name.lower(),
                emotion_valence=es.valence,
                arousal=es.arousal,
                creator_slug=self.creator.slug,
            )

    def _report_event(self, event: StreamEvent):
        """Forward a stream event to the bridge for DB logging."""
        if self._bridge is None:
            return
        try:
            self._bridge.report_event({
                'event_type': event.type.name.lower(),
                'user_name':  getattr(event, 'user', '') or '',
                'message':    getattr(event, 'message', '') or '',
                'amount':     getattr(event, 'amount', 0) or 0,
                'metadata':   getattr(event, 'metadata', {}) or {},
            })
        except Exception as e:
            print(f"[Auto] Bridge report_event failed: {e}")

    def _handle_bridge_command(self, cmd: dict):
        """
        Handle a command received from the dashboard via the bridge.

        Supported actions:
          switch_creator  { slug: str }
          inject_event    { event: { event_type, user_name, message, amount } }
          set_mode        { mode: str }  — hype/chill/focus/roast/wholesome
        """
        action = cmd.get('action')

        if action == 'switch_creator':
            slug = cmd.get('slug', '')
            if slug and self.on_switch_creator is not None:
                print(f"[Auto] Bridge command: switch_creator → {slug}")
                self.on_switch_creator(slug)

        elif action == 'inject_event':
            ev_data = cmd.get('event', {})
            try:
                event = StreamEvent(
                    type=EventType[ev_data.get('event_type', 'chat_message').upper()],
                    user=ev_data.get('user_name', 'dashboard'),
                    message=ev_data.get('message', ''),
                    amount=int(ev_data.get('amount', 0)),
                )
                self.event_queue.put_nowait(event)
                print(f"[Auto] Bridge command: inject_event {event.type.name}")
            except (KeyError, ValueError) as e:
                print(f"[Auto] inject_event bad payload: {e}")

        elif action == 'set_mode':
            mode = cmd.get('mode', '')
            _mode_stimuli = {
                'hype':      (StimulusType.ANTICIPATION, 0.9),
                'chill':     (StimulusType.PLEASANT_STIMULUS, 0.5),
                'focus':     (StimulusType.IDLE, 0.3),
                'roast':     (StimulusType.FUNNY, 0.8),
                'wholesome': (StimulusType.WARMTH, 0.7),
            }
            if mode in _mode_stimuli:
                stim, intensity = _mode_stimuli[mode]
                self.reactions.stimulate(stim, intensity=intensity)
                print(f"[Auto] Bridge command: set_mode → {mode}")

        else:
            print(f"[Auto] Unknown bridge command action: {action}")

    def _create_session(self):
        """POST to /api/sessions to register this stream, store session_id."""
        import urllib.request
        import urllib.error
        body = json.dumps({
            'creator_id': self.creator.slug,
            'platform':   'auto',
        }).encode()
        req = urllib.request.Request(
            f'{self._api_url}/api/sessions',
            data=body,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                self._bridge.session_id = data['id']
                print(f"[Auto] Session created: {data['id']}")
        except Exception as e:
            print(f"[Auto] Could not create session: {e}")

    def _end_session(self):
        """PATCH the session to status=ended on shutdown."""
        import urllib.request
        sid = self._bridge.session_id
        if not sid:
            return
        body = json.dumps({'status': 'ended'}).encode()
        req = urllib.request.Request(
            f'{self._api_url}/api/sessions/{sid}',
            data=body,
            headers={'Content-Type': 'application/json'},
            method='PATCH',
        )
        try:
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass  # Best-effort on shutdown

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
