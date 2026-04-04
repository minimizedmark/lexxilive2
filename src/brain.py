"""
AI Brain – the Claude-powered creator persona engine.

Responsibilities:
  - Holds the creator's personality, knowledge, and speaking style
  - Receives stream events (chat messages, subs, raids, etc.)
  - Decides what to say and when (not every message gets a response)
  - Maintains short-term conversational memory for the session
  - Generates idle commentary when chat is quiet

Each creator's personality lives in their config.json under the
'persona' key (see CreatorPersona for the full schema).
"""

import time
import random
import threading
import queue
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class EventType(Enum):
    CHAT_MESSAGE   = auto()
    SUBSCRIPTION   = auto()
    GIFTED_SUB     = auto()
    RAID           = auto()
    FOLLOW         = auto()
    DONATION       = auto()
    BITS           = auto()
    STREAM_START   = auto()
    STREAM_END     = auto()
    IDLE_TICK      = auto()     # fired when chat has been quiet too long


@dataclass
class StreamEvent:
    type:     EventType
    user:     str = ''
    message:  str = ''
    amount:   int = 0          # bits / donation amount / gifted sub count
    metadata: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class SpeakRequest:
    text:     str
    priority: int = 5          # 1 = highest (events), 10 = lowest (idle)
    event:    StreamEvent | None = None


# ---------------------------------------------------------------------------
# Creator persona definition
# ---------------------------------------------------------------------------

class CreatorPersona:
    """
    Wraps the personality configuration for one creator.
    Builds the Claude system prompt and response policies.

    config.json 'persona' block schema:
    {
      "system_prompt": "You are Lexi, an energetic gaming streamer...",
      "name": "Lexi",
      "speaking_style": "casual, bubbly, uses 'omg', 'lets go', 'that's insane'",
      "topics": ["gaming", "anime", "tech"],
      "catchphrases": ["Lets gooo!", "That's actually insane", "No way!"],
      "chat_response_rate": 0.35,
      "idle_interval_seconds": 45,
      "idle_topics": ["talk about what game you're playing", "react to recent gaming news"],
      "max_response_words": 40,
      "language": "en"
    }
    """

    DEFAULTS = {
        'speaking_style':        'friendly, casual streamer',
        'topics':                ['gaming', 'streaming', 'life'],
        'catchphrases':          [],
        'chat_response_rate':    0.3,
        'idle_interval_seconds': 40,
        'idle_topics':           [
            'mention something interesting about your day',
            'comment on what you are currently doing on stream',
            'ask the chat a fun question',
            'share a hot take about something in your niche',
        ],
        'max_response_words':    45,
        'language':              'en',
    }

    def __init__(self, creator_cfg: dict):
        p = creator_cfg.get('persona', {})
        self.name            = creator_cfg.get('name', 'AI Streamer')
        self.system_prompt   = p.get('system_prompt', self._default_system(p))
        self.speaking_style  = p.get('speaking_style',  self.DEFAULTS['speaking_style'])
        self.topics          = p.get('topics',           self.DEFAULTS['topics'])
        self.catchphrases    = p.get('catchphrases',     self.DEFAULTS['catchphrases'])
        self.response_rate   = float(p.get('chat_response_rate',
                                           self.DEFAULTS['chat_response_rate']))
        self.idle_interval   = float(p.get('idle_interval_seconds',
                                           self.DEFAULTS['idle_interval_seconds']))
        self.idle_topics     = p.get('idle_topics',      self.DEFAULTS['idle_topics'])
        self.max_words       = int(p.get('max_response_words',
                                         self.DEFAULTS['max_response_words']))
        self.language        = p.get('language',         self.DEFAULTS['language'])

    def _default_system(self, p: dict) -> str:
        name   = p.get('name', self.DEFAULTS.get('name', 'AI Streamer'))
        style  = p.get('speaking_style', self.DEFAULTS['speaking_style'])
        topics = ', '.join(p.get('topics', self.DEFAULTS['topics']))
        return (
            f"You are {name}, a live streamer with a {style} personality. "
            f"You love talking about: {topics}. "
            f"You are currently live streaming and interacting with your chat. "
            f"Respond in character — never break the fourth wall. "
            f"Keep all responses under {self.DEFAULTS['max_response_words']} words. "
            f"Sound natural and conversational, not like an AI. "
            f"Never say you are an AI, language model, or assistant."
        )

    def chat_system_prompt(self) -> str:
        extras = ''
        if self.catchphrases:
            extras = f" Occasionally use your catchphrases: {', '.join(self.catchphrases)}."
        return self.system_prompt + extras

    def event_system_prompt(self) -> str:
        return (
            self.system_prompt
            + ' React authentically and enthusiastically to stream events. Keep it short.'
        )


# ---------------------------------------------------------------------------
# Conversation memory
# ---------------------------------------------------------------------------

class ConversationMemory:
    """
    Rolling window of recent messages used as Claude context.
    Older messages are dropped to stay within token budget.
    """

    def __init__(self, max_turns: int = 20):
        self._turns: list[dict] = []
        self.max_turns = max_turns

    def add_user(self, content: str):
        self._turns.append({'role': 'user', 'content': content})
        self._trim()

    def add_assistant(self, content: str):
        self._turns.append({'role': 'assistant', 'content': content})
        self._trim()

    def get_messages(self) -> list[dict]:
        return list(self._turns)

    def clear(self):
        self._turns.clear()

    def _trim(self):
        while len(self._turns) > self.max_turns * 2:
            # Remove oldest pair
            self._turns.pop(0)
            if self._turns:
                self._turns.pop(0)


# ---------------------------------------------------------------------------
# The Brain
# ---------------------------------------------------------------------------

class Brain:
    """
    Processes stream events and generates spoken responses for the creator persona.

    Usage:
        brain = Brain(persona, on_speak=lambda req: tts_queue.put(req))
        brain.start()
        brain.push_event(StreamEvent(EventType.CHAT_MESSAGE, user='Bob', message='hi!'))
        brain.stop()

    The on_speak callback fires whenever the brain decides to say something.
    It receives a SpeakRequest with the text and priority.
    """

    def __init__(
        self,
        persona: CreatorPersona,
        on_speak: Callable[[SpeakRequest], None],
        model: str = 'claude-opus-4-6',
    ):
        if not ANTHROPIC_AVAILABLE:
            print("[Brain] anthropic package not installed.  pip install anthropic")

        self.persona   = persona
        self.on_speak  = on_speak
        self.model     = model

        self._client   = anthropic.Anthropic() if ANTHROPIC_AVAILABLE else None
        self._memory   = ConversationMemory()
        self._event_q: queue.Queue[StreamEvent] = queue.Queue()
        self._running  = False
        self._thread: threading.Thread | None = None

        # Idle timer
        self._last_speech_time = time.time()
        self._idle_check_interval = 5.0   # seconds between idle checks

        # Chat rate limiting
        self._last_chat_response = 0.0
        self._min_chat_gap = 8.0          # minimum seconds between chat responses

        # Currently speaking flag (set by caller to avoid overlapping)
        self.is_speaking = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push_event(self, event: StreamEvent):
        """Thread-safe: add an event to be processed."""
        self._event_q.put_nowait(event)

    def start(self):
        if not ANTHROPIC_AVAILABLE:
            print("[Brain] Cannot start: anthropic not installed.")
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name='brain')
        self._thread.start()
        print(f"[Brain] Started persona: {self.persona.name}")

    def stop(self):
        self._running = False
        self._event_q.put_nowait(None)   # unblock
        if self._thread:
            self._thread.join(timeout=5)
        print("[Brain] Stopped.")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _loop(self):
        last_idle_check = time.time()

        while self._running:
            # --- Process queued events ---
            try:
                event = self._event_q.get(timeout=self._idle_check_interval)
            except queue.Empty:
                event = None

            if event is None:
                if not self._running:
                    break
            else:
                self._handle_event(event)

            # --- Idle commentary ---
            now = time.time()
            if now - last_idle_check >= self._idle_check_interval:
                last_idle_check = now
                if (now - self._last_speech_time >= self.persona.idle_interval
                        and not self.is_speaking):
                    self._generate_idle()

    # ------------------------------------------------------------------
    # Event routing
    # ------------------------------------------------------------------

    def _handle_event(self, event: StreamEvent):
        if event.type == EventType.CHAT_MESSAGE:
            self._handle_chat(event)
        elif event.type == EventType.SUBSCRIPTION:
            self._handle_subscription(event)
        elif event.type == EventType.GIFTED_SUB:
            self._handle_gifted_sub(event)
        elif event.type == EventType.RAID:
            self._handle_raid(event)
        elif event.type == EventType.FOLLOW:
            self._handle_follow(event)
        elif event.type == EventType.DONATION:
            self._handle_donation(event)
        elif event.type == EventType.BITS:
            self._handle_bits(event)
        elif event.type == EventType.STREAM_START:
            self._handle_stream_start(event)

    def _handle_chat(self, event: StreamEvent):
        now = time.time()
        # Rate limit: don't respond to every message
        gap = now - self._last_chat_response
        if gap < self._min_chat_gap:
            return
        # Probabilistic filter
        if random.random() > self.persona.response_rate:
            return
        # Skip very short / spammy messages
        if len(event.message.strip()) < 4:
            return

        prompt = f"{event.user} says: {event.message}"
        response = self._ask(prompt, system=self.persona.chat_system_prompt())
        if response:
            self._memory.add_user(prompt)
            self._memory.add_assistant(response)
            self._speak(response, priority=5, event=event)
            self._last_chat_response = now

    def _handle_subscription(self, event: StreamEvent):
        months = event.metadata.get('months', 1)
        prompt = (f"{event.user} just subscribed! "
                  f"{'They have been subscribed for ' + str(months) + ' months!' if months > 1 else ''} "
                  f"React with genuine excitement in under 20 words.")
        response = self._ask(prompt, system=self.persona.event_system_prompt(),
                             use_memory=False)
        if response:
            self._speak(response, priority=2, event=event)

    def _handle_gifted_sub(self, event: StreamEvent):
        prompt = (f"{event.user} just gifted {event.amount} subscriptions to the channel! "
                  f"Thank them enthusiastically in under 25 words.")
        response = self._ask(prompt, system=self.persona.event_system_prompt(),
                             use_memory=False)
        if response:
            self._speak(response, priority=1, event=event)

    def _handle_raid(self, event: StreamEvent):
        prompt = (f"{event.user} is raiding with {event.amount} viewers! "
                  f"Welcome them to the stream in under 30 words.")
        response = self._ask(prompt, system=self.persona.event_system_prompt(),
                             use_memory=False)
        if response:
            self._speak(response, priority=1, event=event)

    def _handle_follow(self, event: StreamEvent):
        # Only respond to ~40% of follows to avoid spam
        if random.random() > 0.4:
            return
        prompt = f"{event.user} just followed! Say a quick welcome in under 12 words."
        response = self._ask(prompt, system=self.persona.event_system_prompt(),
                             use_memory=False)
        if response:
            self._speak(response, priority=4, event=event)

    def _handle_donation(self, event: StreamEvent):
        prompt = (f"{event.user} just donated ${event.amount:.2f}! "
                  f"Message: '{event.message}'. React and respond in under 30 words.")
        response = self._ask(prompt, system=self.persona.event_system_prompt(),
                             use_memory=False)
        if response:
            self._speak(response, priority=1, event=event)

    def _handle_bits(self, event: StreamEvent):
        prompt = (f"{event.user} cheered {event.amount} bits! "
                  f"Thank them in under 15 words.")
        response = self._ask(prompt, system=self.persona.event_system_prompt(),
                             use_memory=False)
        if response:
            self._speak(response, priority=3, event=event)

    def _handle_stream_start(self, event: StreamEvent):
        prompt = ("You are starting your stream. Give an energetic opening "
                  "greeting to your audience in under 40 words.")
        response = self._ask(prompt, system=self.persona.event_system_prompt(),
                             use_memory=False)
        if response:
            self._speak(response, priority=1, event=event)

    def _generate_idle(self):
        topic = random.choice(self.persona.idle_topics)
        prompt = (f"You've been quiet for a bit. {topic}. "
                  f"Keep it under {self.persona.max_words} words and sound natural.")
        response = self._ask(prompt, system=self.persona.chat_system_prompt())
        if response:
            self._memory.add_assistant(response)
            self._speak(response, priority=8)

    # ------------------------------------------------------------------
    # Claude API call
    # ------------------------------------------------------------------

    def _ask(self, prompt: str, system: str,
             use_memory: bool = True) -> str | None:
        if self._client is None:
            return None
        try:
            messages = self._memory.get_messages() if use_memory else []
            messages = messages + [{'role': 'user', 'content': prompt}]

            resp = self._client.messages.create(
                model=self.model,
                max_tokens=200,
                system=system,
                messages=messages,
            )
            text = resp.content[0].text.strip()
            # Enforce word limit
            words = text.split()
            if len(words) > self.persona.max_words + 10:
                text = ' '.join(words[:self.persona.max_words]) + '…'
            return text
        except Exception as e:
            print(f"[Brain] Claude API error: {e}")
            return None

    # ------------------------------------------------------------------
    # Speak helper
    # ------------------------------------------------------------------

    def _speak(self, text: str, priority: int, event: StreamEvent | None = None):
        self._last_speech_time = time.time()
        req = SpeakRequest(text=text, priority=priority, event=event)
        self.on_speak(req)
