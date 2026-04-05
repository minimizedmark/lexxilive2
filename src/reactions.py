"""
Human behavioral reaction system.

Models the full stack of human physical/emotional responses to stimuli:

  EmotionalState     – valence × arousal 2D space with momentum
  MicroExpression    – involuntary sub-200ms expression flashes (FACS-based)
  AutonomicSimulator – breathing rate, blink frequency, heart-rate proxy
  StimulusProcessor  – maps incoming stimuli to state changes
  ReactionEngine     – orchestrates everything, drives AnimationController

Based on:
  - FACS (Facial Action Coding System, Ekman & Friesen)
  - James-Lange theory (physiological state drives emotional experience)
  - Circumplex model of affect (Russell 1980)
  - Autonomic nervous system arousal research
"""

import time
import math
import random
import threading
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable

from .animation import AnimationController, Reaction


# ---------------------------------------------------------------------------
# Emotional state space  (Russell's circumplex model)
# ---------------------------------------------------------------------------
#
#  Arousal
#    1.0  ┤  tense  excited  elated
#    0.5  ┤
#    0.0  ┤  bored  neutral  content
#   -0.5  ┤
#   -1.0  ┤  depressed  sad  calm
#          └──────────────────────────── Valence
#            -1.0    0.0    +1.0

@dataclass
class EmotionalState:
    """
    Continuous 2D emotional state with momentum and decay toward neutral.

    valence:  -1.0 (negative/unpleasant)  → +1.0 (positive/pleasant)
    arousal:  -1.0 (calm/sleepy)          → +1.0 (excited/tense)
    """
    valence:  float = 0.1   # slightly positive baseline
    arousal:  float = 0.0

    # Velocities (state drifts with momentum)
    _dv: float = field(default=0.0, repr=False)
    _da: float = field(default=0.0, repr=False)

    # How fast state decays back toward baseline (per second)
    DECAY_V:    float = field(default=0.08, repr=False)
    DECAY_A:    float = field(default=0.12, repr=False)
    BASELINE_V: float = field(default=0.1,  repr=False)
    BASELINE_A: float = field(default=0.0,  repr=False)

    def push(self, dvalence: float = 0.0, darousal: float = 0.0):
        """Apply an instantaneous stimulus to emotional state."""
        self._dv += dvalence
        self._da += darousal

    def update(self, dt: float):
        # Apply velocity
        self.valence = float(_clamp(self.valence + self._dv * dt, -1, 1))
        self.arousal = float(_clamp(self.arousal + self._da * dt, -1, 1))
        # Decay velocity
        self._dv *= max(0, 1.0 - dt * 3.0)
        self._da *= max(0, 1.0 - dt * 3.0)
        # Decay state toward baseline
        self.valence += (self.BASELINE_V - self.valence) * self.DECAY_V * dt
        self.arousal += (self.BASELINE_A - self.arousal) * self.DECAY_A * dt

    @property
    def label(self) -> str:
        v, a = self.valence, self.arousal
        if a > 0.5:
            return 'excited' if v > 0 else 'tense'
        if a > 0.15:
            return 'happy' if v > 0.3 else ('curious' if v > -0.1 else 'worried')
        if a < -0.3:
            return 'calm' if v > 0 else 'sad'
        if v > 0.5:
            return 'content'
        if v < -0.3:
            return 'displeased'
        return 'neutral'

    @property
    def intensity(self) -> float:
        """Distance from neutral — 0.0 to ~1.4"""
        return math.sqrt(self.valence ** 2 + self.arousal ** 2)


# ---------------------------------------------------------------------------
# Micro-expressions  (Ekman: involuntary, 40–200 ms, before suppression)
# ---------------------------------------------------------------------------

MICRO_EXPRESSIONS = {
    # name: (expression_tag, duration_s, trigger_conditions)
    'surprise_flash':   ('surprised', 0.12, lambda v, a: a > 0.5 and abs(v) < 0.4),
    'disgust_micro':    ('sad',        0.08, lambda v, a: v < -0.5),
    'joy_micro':        ('happy',      0.15, lambda v, a: v > 0.6 and a > 0.2),
    'fear_flash':       ('surprised',  0.10, lambda v, a: v < -0.3 and a > 0.4),
    'contempt_micro':   ('thinking',   0.09, lambda v, a: v < -0.2 and a < 0.1),
    'interest_flash':   ('thinking',   0.13, lambda v, a: v > 0.1 and 0.1 < a < 0.5),
}


class MicroExpressionSystem:
    """
    Fires brief involuntary expression flashes when emotional state crosses
    thresholds.  These precede the conscious/composed expression by design.
    """

    REFRACTORY_PERIOD = 0.8    # minimum seconds between micro-expressions

    def __init__(self, expression_layer):
        self._expr_layer   = expression_layer
        self._last_micro_t = 0.0
        self._active_until = 0.0
        self._prev_expression = 'neutral'

    def update(self, state: EmotionalState):
        now = time.perf_counter()
        if now < self._last_micro_t + self.REFRACTORY_PERIOD:
            return
        # Revert after duration
        if self._active_until > 0 and now > self._active_until:
            self._expr_layer.set(self._prev_expression)
            self._active_until = 0.0
            return

        for name, (tag, dur, cond) in MICRO_EXPRESSIONS.items():
            if cond(state.valence, state.arousal):
                # Only fire if different from current
                if tag != self._prev_expression:
                    self._prev_expression = self._expr_layer._target
                    self._expr_layer.set(tag, instant=True)
                    self._active_until = now + dur
                    self._last_micro_t = now
                    break


# ---------------------------------------------------------------------------
# Autonomic simulation
# ---------------------------------------------------------------------------

class AutonomicSimulator:
    """
    Simulates autonomic nervous system responses that modulate animation:

    - Breathing rate:  12 bpm (calm) → 24 bpm (high arousal)
    - Blink rate:      15/min (neutral) → 25/min (stress) → 6/min (focus)
    - Heart rate proxy: drives subtle animation timing jitter
    - Postural sway:   amplitude increases with arousal

    These feed directly into AvatarPhysics parameters.
    """

    def __init__(self, physics):
        self._physics      = physics
        self._last_blink_t = time.perf_counter()
        self._last_breath_t = time.perf_counter()

    def update(self, state: EmotionalState, dt: float):
        arousal = state.arousal
        valence = state.valence

        # --- Breathing rate ---
        bpm = _lerp(12, 26, _clamp((arousal + 1) / 2, 0, 1))
        freq = bpm / 60.0
        self._physics.x._breath_freq = freq    # type: ignore[attr-defined]
        breath_amp = _lerp(2.0, 7.0, _clamp((arousal + 1) / 2, 0, 1))
        self._physics.x._breath_amp = breath_amp

        # --- Sway amplitude --- (more restless when high arousal)
        sway = _lerp(0.3, 1.2, _clamp((arousal + 1) / 2, 0, 1))
        self._physics.x.stiffness = _lerp(320, 180, _clamp((arousal + 1) / 2, 0, 1))

        # --- Spontaneous micro-movements ---
        if random.random() < 0.015 * (1.0 + arousal):
            self._physics.impulse(
                x=random.uniform(-8, 8) * sway,
                y=random.uniform(-4, 4) * sway,
                rotation=random.uniform(-1.5, 1.5) * sway,
            )

        # --- Blink timing ---
        now = time.perf_counter()
        # High arousal = more blinks; deep focus = fewer
        blinks_per_min = _lerp(15, 28, _clamp((arousal + 1) / 2, 0, 1))
        blink_interval = 60.0 / blinks_per_min + random.uniform(-0.5, 0.5)
        if now - self._last_blink_t > blink_interval:
            # Signal a blink by directly setting the blink timer
            # (AvatarPhysics breathing handles this via AnimationState.blink)
            self._last_blink_t = now


# ---------------------------------------------------------------------------
# Stimulus catalogue
# ---------------------------------------------------------------------------

class StimulusType(Enum):
    # Social / chat
    COMPLIMENT         = auto()
    INSULT             = auto()
    FUNNY              = auto()
    QUESTION           = auto()
    SHOCKING_NEWS      = auto()
    WHOLESOME          = auto()
    CRINGE             = auto()

    # Stream events
    RAID               = auto()
    SUBSCRIPTION       = auto()
    GIFTED_SUB         = auto()
    DONATION           = auto()
    BITS               = auto()
    FOLLOW             = auto()
    HOST               = auto()

    # Sensory / environmental (for physical response realism)
    LOUD_NOISE         = auto()    # startle reflex
    SUDDEN_TOUCH       = auto()    # flinch / goosebumps
    WARMTH             = auto()    # relaxation response
    COLD               = auto()    # tension / shiver
    PLEASANT_STIMULUS  = auto()    # positive arousal
    UNPLEASANT_STIMULUS = auto()   # negative arousal + withdrawal
    TICKLE             = auto()    # involuntary laugh
    ANTICIPATION       = auto()    # building arousal before event

    # Internal
    IDLE               = auto()
    BOREDOM            = auto()
    FATIGUE            = auto()


# Mapping: stimulus → (dvalence, darousal, reaction, micro_probability)
STIMULUS_MAP: dict[StimulusType, tuple] = {
    StimulusType.COMPLIMENT:          ( 0.6,  0.3, Reaction.EXCITED,   0.7),
    StimulusType.INSULT:              (-0.5,  0.4, Reaction.SURPRISED,  0.9),
    StimulusType.FUNNY:               ( 0.5,  0.4, Reaction.LAUGHING,   0.3),
    StimulusType.QUESTION:            ( 0.1,  0.2, Reaction.THINKING,   0.4),
    StimulusType.SHOCKING_NEWS:       (-0.1,  0.7, Reaction.SURPRISED,  0.95),
    StimulusType.WHOLESOME:           ( 0.7,  0.2, Reaction.SUB,        0.5),
    StimulusType.CRINGE:              (-0.3,  0.2, Reaction.THINKING,   0.6),

    StimulusType.RAID:                ( 0.8,  0.9, Reaction.RAID,       0.2),
    StimulusType.SUBSCRIPTION:        ( 0.7,  0.6, Reaction.SUB,        0.2),
    StimulusType.GIFTED_SUB:          ( 0.8,  0.7, Reaction.CELEBRATE,  0.2),
    StimulusType.DONATION:            ( 0.6,  0.8, Reaction.DONATION,   0.3),
    StimulusType.BITS:                ( 0.5,  0.5, Reaction.BITS,       0.3),
    StimulusType.FOLLOW:              ( 0.4,  0.3, Reaction.FOLLOW,     0.4),
    StimulusType.HOST:                ( 0.7,  0.7, Reaction.CELEBRATE,  0.2),

    # Physical / sensory
    StimulusType.LOUD_NOISE:          (-0.1,  0.9, Reaction.SURPRISED,  0.99),
    StimulusType.SUDDEN_TOUCH:        ( 0.0,  0.8, Reaction.SURPRISED,  0.95),
    StimulusType.WARMTH:              ( 0.4, -0.3, Reaction.IDLE,       0.2),
    StimulusType.COLD:                (-0.2,  0.3, Reaction.SURPRISED,  0.6),
    StimulusType.PLEASANT_STIMULUS:   ( 0.7,  0.5, Reaction.EXCITED,    0.4),
    StimulusType.UNPLEASANT_STIMULUS: (-0.6,  0.4, Reaction.SURPRISED,  0.8),
    StimulusType.TICKLE:              ( 0.3,  0.7, Reaction.LAUGHING,   0.9),
    StimulusType.ANTICIPATION:        ( 0.3,  0.6, Reaction.THINKING,   0.3),

    StimulusType.IDLE:                ( 0.0,  0.0, Reaction.IDLE,       0.0),
    StimulusType.BOREDOM:             (-0.1, -0.3, Reaction.THINKING,   0.1),
    StimulusType.FATIGUE:             ( 0.0, -0.4, Reaction.SAD,        0.1),
}


# ---------------------------------------------------------------------------
# Reaction engine
# ---------------------------------------------------------------------------

class ReactionEngine:
    """
    Full human behavioral simulation.

    Usage:
        engine = ReactionEngine(animation_controller)
        engine.start()

        # From any thread:
        engine.stimulate(StimulusType.LOUD_NOISE)
        engine.stimulate(StimulusType.COMPLIMENT, intensity=1.4)
        engine.set_emotion_from_text("omg that's hilarious lmao")

    The engine drives AnimationController every frame via its update loop.
    """

    EMOTION_KEYWORDS: dict[str, tuple[float, float]] = {
        # word: (dvalence, darousal)
        'haha':       ( 0.5,  0.4),
        'lmao':       ( 0.6,  0.5),
        'lol':        ( 0.4,  0.3),
        'omg':        ( 0.1,  0.8),
        'no way':     ( 0.0,  0.7),
        'wow':        ( 0.2,  0.6),
        'aww':        ( 0.6,  0.2),
        'that\'s insane': (0.2, 0.7),
        'lets go':    ( 0.7,  0.8),
        'yay':        ( 0.7,  0.5),
        'ugh':        (-0.4,  0.3),
        'ew':         (-0.5,  0.3),
        'boring':     (-0.2, -0.4),
        'tired':      ( 0.0, -0.5),
        'scared':     (-0.3,  0.7),
        'nervous':    (-0.2,  0.5),
        'excited':    ( 0.6,  0.7),
        'happy':      ( 0.7,  0.2),
        'sad':        (-0.5, -0.1),
        'angry':      (-0.4,  0.6),
        'frustrated': (-0.4,  0.4),
        'confused':   (-0.1,  0.3),
        'surprised':  ( 0.1,  0.7),
        'curious':    ( 0.3,  0.4),
        'love':       ( 0.8,  0.3),
        'hate':       (-0.8,  0.5),
        'miss':       (-0.2,  0.1),
    }

    def __init__(
        self,
        animation_ctrl: AnimationController,
        on_state_change: Callable | None = None,
    ):
        self.anim            = animation_ctrl
        self.state           = EmotionalState()
        self.on_state_change = on_state_change

        self._micro   = MicroExpressionSystem(animation_ctrl.expression)
        self._auto    = AutonomicSimulator(animation_ctrl.physics)

        self._running = False
        self._thread: threading.Thread | None = None
        self._lock    = threading.Lock()

        self._last_label  = 'neutral'
        self._last_update = time.perf_counter()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop, daemon=True, name='reaction-engine')
        self._thread.start()
        print("[Reactions] Engine started.")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def stimulate(self, stimulus: StimulusType, intensity: float = 1.0):
        """
        Apply a stimulus.  intensity multiplies the emotional push.
        Safe to call from any thread.
        """
        entry = STIMULUS_MAP.get(stimulus)
        if entry is None:
            return
        dv, da, reaction, micro_p = entry

        with self._lock:
            self.state.push(dvalence=dv * intensity, darousal=da * intensity)

        # Trigger physical animation reaction
        self.anim.trigger(reaction)

        # Optionally fire a micro-expression with given probability
        if random.random() < micro_p:
            self._micro._last_micro_t = 0   # reset refractory to allow immediate fire

        if self.on_state_change:
            try:
                self.on_state_change(self.state)
            except Exception:
                pass

    def stimulate_raw(self, dvalence: float, darousal: float,
                      reaction: Reaction | None = None):
        """Direct push into emotional space without stimulus lookup."""
        with self._lock:
            self.state.push(dvalence=dvalence, darousal=darousal)
        if reaction:
            self.anim.trigger(reaction)

    def set_emotion_from_text(self, text: str):
        """
        Parse text for emotional keywords and push state accordingly.
        Called by Brain after generating each response.
        """
        text_lower = text.lower()
        total_v, total_a = 0.0, 0.0
        hits = 0
        for kw, (dv, da) in self.EMOTION_KEYWORDS.items():
            if kw in text_lower:
                total_v += dv
                total_a += da
                hits += 1
        if hits:
            # Average, then apply
            self.stimulate_raw(total_v / hits * 0.6, total_a / hits * 0.6)

    def set_emotion_from_speech_rate(self, words_per_second: float):
        """Fast speech → higher arousal."""
        normal_wps = 2.5
        ratio = (words_per_second / normal_wps) - 1.0
        self.stimulate_raw(0.0, _clamp(ratio * 0.3, -0.3, 0.4))

    @property
    def current_label(self) -> str:
        return self.state.label

    @property
    def valence(self) -> float:
        return self.state.valence

    @property
    def arousal(self) -> float:
        return self.state.arousal

    # ------------------------------------------------------------------
    # Internal update loop
    # ------------------------------------------------------------------

    def _loop(self):
        while self._running:
            now = time.perf_counter()
            dt  = min(now - self._last_update, 0.05)
            self._last_update = now

            with self._lock:
                self.state.update(dt)
                state_copy = EmotionalState(
                    valence=self.state.valence,
                    arousal=self.state.arousal,
                )

            # Micro-expressions
            self._micro.update(state_copy)

            # Autonomic (breathing, blink, sway)
            self._auto.update(state_copy, dt)

            # Notify on meaningful state change
            new_label = state_copy.label
            if new_label != self._last_label:
                self._last_label = new_label
                if self.on_state_change:
                    try:
                        self.on_state_change(state_copy)
                    except Exception:
                        pass

            time.sleep(0.016)   # ~60 Hz update rate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))

def _lerp(a, b, t):
    return a + (b - a) * t
