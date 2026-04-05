"""
Avatar animation system with spring physics and expression management.

Provides:
  Transform          – position / scale / rotation state
  SpringAxis         – single-axis mass-spring-damper
  AvatarPhysics      – full 2D physics rig (x, y, scale, rotation)
  ExpressionLayer    – per-frame expression overlay blending
  ParticleSystem     – lightweight particle effects (confetti, hearts, etc.)
  AnimationController – high-level API: trigger named reactions, update each frame
"""

import time
import math
import random
import threading
import numpy as np
import cv2
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable


# ---------------------------------------------------------------------------
# Reaction catalogue
# ---------------------------------------------------------------------------

class Reaction(Enum):
    IDLE        = auto()   # gentle breathing sway
    TALKING     = auto()   # subtle nod while speaking
    EXCITED     = auto()   # quick upward bounce + scale pulse
    SURPRISED   = auto()   # sharp jump + rotation snap
    LAUGHING    = auto()   # rapid horizontal shake
    WAVING      = auto()   # slow left-right sway
    THINKING    = auto()   # slight tilt + forward lean
    SAD         = auto()   # slow droop
    CELEBRATE   = auto()   # big bounce + spin + confetti
    RAID        = auto()   # maximum hype: jump + flash + confetti
    SUB         = auto()   # happy bounce + hearts
    FOLLOW      = auto()   # small wave
    DONATION    = auto()   # surprised → excited sequence
    BITS        = auto()   # quick excited pop
    TILT_LEFT   = auto()   # mirror operator head tilt left
    TILT_RIGHT  = auto()   # mirror operator head tilt right


# ---------------------------------------------------------------------------
# Physics
# ---------------------------------------------------------------------------

@dataclass
class SpringAxis:
    """Single-axis spring-damper.  Call update(dt) every frame."""
    position: float = 0.0
    velocity: float = 0.0
    target:   float = 0.0
    stiffness: float = 280.0
    damping:   float = 22.0
    mass:      float = 1.0

    def update(self, dt: float) -> float:
        force      = (self.target - self.position) * self.stiffness
        force     -= self.velocity * self.damping
        accel      = force / self.mass
        self.velocity += accel * dt
        self.position += self.velocity * dt
        return self.position

    def impulse(self, velocity: float):
        """Apply an instant velocity kick."""
        self.velocity += velocity

    def snap_to(self, value: float):
        self.position = value
        self.velocity = 0.0

    @property
    def at_rest(self) -> bool:
        return abs(self.velocity) < 0.5 and abs(self.position - self.target) < 0.5


@dataclass
class Transform:
    """Current visual transform applied to the avatar each frame."""
    x:        float = 0.0    # pixels right
    y:        float = 0.0    # pixels down
    scale:    float = 1.0    # multiplier
    rotation: float = 0.0    # degrees clockwise

    def copy(self) -> 'Transform':
        return Transform(self.x, self.y, self.scale, self.rotation)


class AvatarPhysics:
    """
    Spring-physics rig for all four avatar axes.
    Idle 'breathing' animation runs automatically.
    """

    def __init__(self):
        self.x    = SpringAxis(stiffness=320, damping=24)
        self.y    = SpringAxis(stiffness=320, damping=24)
        self.s    = SpringAxis(stiffness=400, damping=30, position=1.0, target=1.0)
        self.r    = SpringAxis(stiffness=260, damping=20)

        self._last_t   = time.perf_counter()
        self._breath_t = 0.0          # breathing phase (radians)
        self._breath_amp = 3.0        # pixels
        self._breath_freq = 0.4       # Hz

        self._lock = threading.Lock()

    def update(self) -> Transform:
        now = time.perf_counter()
        dt  = min(now - self._last_t, 0.05)   # cap at 50 ms to avoid explosions
        self._last_t = now

        with self._lock:
            self._breath_t += dt * self._breath_freq * 2 * math.pi
            breath_y = math.sin(self._breath_t) * self._breath_amp

            return Transform(
                x=self.x.update(dt),
                y=self.y.update(dt) + breath_y,
                scale=self.s.update(dt),
                rotation=self.r.update(dt),
            )

    def impulse(self, x=0.0, y=0.0, scale=0.0, rotation=0.0):
        with self._lock:
            self.x.impulse(x)
            self.y.impulse(y)
            self.s.impulse(scale)
            self.r.impulse(rotation)

    def set_target(self, x=None, y=None, scale=None, rotation=None):
        with self._lock:
            if x        is not None: self.x.target = x
            if y        is not None: self.y.target = y
            if scale    is not None: self.s.target = scale
            if rotation is not None: self.r.target = rotation

    def reset_targets(self):
        self.set_target(x=0, y=0, scale=1.0, rotation=0)

    def snap(self):
        with self._lock:
            for ax in (self.x, self.y, self.s, self.r):
                ax.snap_to(ax.target)


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------

EXPRESSION_NAMES = ['neutral', 'happy', 'surprised', 'laughing',
                    'sad', 'thinking', 'excited']


class ExpressionLayer:
    """
    Blends expression overlays onto the avatar.
    Expressions are simple geometric modifications (brightness, eye shape,
    mouth shape) applied via OpenCV.  No separate image assets needed.
    """

    def __init__(self):
        self._current  = 'neutral'
        self._target   = 'neutral'
        self._blend    = 1.0           # 0 = current, 1 = target
        self._blend_spd = 0.12         # blend speed per frame

    def set(self, expression: str, instant: bool = False):
        if expression not in EXPRESSION_NAMES:
            expression = 'neutral'
        if expression == self._target:
            return
        self._current = self._target
        self._target  = expression
        self._blend   = 1.0 if instant else 0.0

    def apply(self, frame: np.ndarray) -> np.ndarray:
        """Apply expression modifications to avatar frame (RGBA uint8)."""
        # Advance blend
        self._blend = min(1.0, self._blend + self._blend_spd)

        if self._target == 'neutral' and self._blend >= 1.0:
            return frame   # fast path

        out = frame.copy()

        # --- Brightness (excitement / sadness) ---
        if self._target in ('happy', 'excited', 'laughing'):
            boost = int(15 * self._blend)
            out[:, :, :3] = np.clip(out[:, :, :3].astype(int) + boost, 0, 255)
        elif self._target == 'sad':
            out[:, :, :3] = (out[:, :, :3].astype(float)
                             * (1.0 - 0.15 * self._blend)).astype(np.uint8)

        # --- Tint for surprise (slight blue-white flash) ---
        if self._target == 'surprised':
            tint = np.zeros_like(out)
            tint[:, :, 0] = 30    # R
            tint[:, :, 1] = 30    # G
            tint[:, :, 2] = 60    # B
            alpha = self._blend * 0.3
            out[:, :, :3] = np.clip(
                out[:, :, :3].astype(float) + tint[:, :, :3] * alpha,
                0, 255).astype(np.uint8)

        return out


# ---------------------------------------------------------------------------
# Particles
# ---------------------------------------------------------------------------

@dataclass
class Particle:
    x:    float
    y:    float
    vx:   float
    vy:   float
    life: float    # seconds remaining
    max_life: float
    size: int
    color: tuple   # BGR
    shape: str     # 'circle' | 'heart' | 'star' | 'square'


class ParticleSystem:
    """
    Lightweight CPU particle effects rendered into the video frame.
    """

    GRAVITY   = 280.0    # px/s²
    MAX_PARTS = 200

    def __init__(self):
        self._particles: list[Particle] = []
        self._lock = threading.Lock()
        self._last_t = time.perf_counter()

    # ------------------------------------------------------------------
    # Emitters
    # ------------------------------------------------------------------

    def burst_confetti(self, cx: int, cy: int, count: int = 60):
        colors = [(255,80,80),(80,255,80),(80,80,255),
                  (255,255,80),(255,80,255),(80,255,255),(255,160,40)]
        with self._lock:
            for _ in range(count):
                angle = random.uniform(-math.pi, 0)   # upward half
                speed = random.uniform(150, 450)
                p = Particle(
                    x=cx, y=cy,
                    vx=math.cos(angle) * speed * random.uniform(0.5, 1.5),
                    vy=math.sin(angle) * speed,
                    life=random.uniform(1.2, 2.5),
                    max_life=2.5,
                    size=random.randint(4, 10),
                    color=random.choice(colors),
                    shape=random.choice(['square', 'circle']),
                )
                self._particles.append(p)

    def burst_hearts(self, cx: int, cy: int, count: int = 20):
        with self._lock:
            for _ in range(count):
                p = Particle(
                    x=cx + random.randint(-60, 60),
                    y=cy,
                    vx=random.uniform(-40, 40),
                    vy=random.uniform(-120, -60),
                    life=random.uniform(1.5, 2.5),
                    max_life=2.5,
                    size=random.randint(8, 18),
                    color=(random.randint(180, 255),
                           random.randint(60, 120),
                           random.randint(180, 255)),
                    shape='heart',
                )
                self._particles.append(p)

    def burst_stars(self, cx: int, cy: int, count: int = 30):
        with self._lock:
            for _ in range(count):
                angle = random.uniform(0, 2 * math.pi)
                speed = random.uniform(80, 300)
                p = Particle(
                    x=cx, y=cy,
                    vx=math.cos(angle) * speed,
                    vy=math.sin(angle) * speed,
                    life=random.uniform(0.8, 1.6),
                    max_life=1.6,
                    size=random.randint(5, 12),
                    color=(random.randint(200, 255),
                           random.randint(200, 255),
                           random.randint(40, 100)),
                    shape='star',
                )
                self._particles.append(p)

    def burst_coins(self, cx: int, cy: int, count: int = 25):
        with self._lock:
            for _ in range(count):
                angle = random.uniform(-math.pi * 0.8, -math.pi * 0.2)
                speed = random.uniform(120, 320)
                p = Particle(
                    x=cx, y=cy,
                    vx=math.cos(angle) * speed,
                    vy=math.sin(angle) * speed,
                    life=random.uniform(1.0, 2.0),
                    max_life=2.0,
                    size=random.randint(6, 12),
                    color=(40, 200, 220),   # gold-ish in BGR
                    shape='circle',
                )
                self._particles.append(p)

    # ------------------------------------------------------------------
    # Update + render
    # ------------------------------------------------------------------

    def update_and_draw(self, frame: np.ndarray) -> np.ndarray:
        now = time.perf_counter()
        dt  = min(now - self._last_t, 0.05)
        self._last_t = now

        with self._lock:
            alive = []
            for p in self._particles:
                p.vy  += self.GRAVITY * dt
                p.x   += p.vx * dt
                p.y   += p.vy * dt
                p.life -= dt
                if p.life > 0:
                    alive.append(p)
            self._particles = alive[-self.MAX_PARTS:]
            particles = list(self._particles)

        if not particles:
            return frame

        out = frame.copy()
        for p in particles:
            alpha = min(1.0, p.life / (p.max_life * 0.3))
            ix, iy = int(p.x), int(p.y)
            if not (0 <= ix < out.shape[1] and 0 <= iy < out.shape[0]):
                continue
            color = tuple(int(c * alpha) for c in p.color)
            self._draw_particle(out, ix, iy, p.size, color, p.shape)

        return out

    @staticmethod
    def _draw_particle(frame, x, y, size, color, shape):
        if shape == 'circle':
            cv2.circle(frame, (x, y), size // 2, color, -1, cv2.LINE_AA)
        elif shape == 'square':
            h = size // 2
            cv2.rectangle(frame, (x - h, y - h), (x + h, y + h), color, -1)
        elif shape == 'heart':
            # Simple heart approximation: two circles + triangle
            r = max(2, size // 3)
            cv2.circle(frame, (x - r, y - r), r, color, -1)
            cv2.circle(frame, (x + r, y - r), r, color, -1)
            pts = np.array([[x - size // 2, y - r // 2],
                            [x + size // 2, y - r // 2],
                            [x, y + size // 2]], np.int32)
            cv2.fillPoly(frame, [pts], color)
        elif shape == 'star':
            pts = _star_points(x, y, size)
            cv2.fillPoly(frame, [pts], color)


def _star_points(cx, cy, size) -> np.ndarray:
    pts = []
    for i in range(5):
        outer = 2 * math.pi * i / 5 - math.pi / 2
        inner = outer + math.pi / 5
        pts.append([cx + int(size * math.cos(outer)),
                    cy + int(size * math.sin(outer))])
        pts.append([cx + int(size * 0.4 * math.cos(inner)),
                    cy + int(size * 0.4 * math.sin(inner))])
    return np.array(pts, np.int32)


# ---------------------------------------------------------------------------
# Animation controller
# ---------------------------------------------------------------------------

_REACTION_SCRIPTS: dict[Reaction, dict] = {
    Reaction.IDLE: {
        'impulse': {},
        'expression': 'neutral',
        'particles': None,
        'duration': 0,
    },
    Reaction.TALKING: {
        'impulse': {'y': -18},
        'expression': 'neutral',
        'particles': None,
        'duration': 0.15,
    },
    Reaction.EXCITED: {
        'impulse': {'y': -80, 'scale': 0.08},
        'expression': 'excited',
        'particles': None,
        'duration': 0.6,
    },
    Reaction.SURPRISED: {
        'impulse': {'y': -120, 'scale': 0.12, 'rotation': 8},
        'expression': 'surprised',
        'particles': None,
        'duration': 0.4,
    },
    Reaction.LAUGHING: {
        'impulse': {'x': 60, 'rotation': -6},
        'expression': 'laughing',
        'sequence': [
            (0.06, {'x': -120, 'rotation': 12}),
            (0.12, {'x': 120, 'rotation': -12}),
            (0.18, {'x': -60,  'rotation': 8}),
            (0.24, {'x': 60,   'rotation': -4}),
        ],
        'particles': None,
        'duration': 0.5,
    },
    Reaction.WAVING: {
        'impulse': {'x': 40, 'rotation': -10},
        'expression': 'happy',
        'sequence': [
            (0.15, {'x': -80, 'rotation': 15}),
            (0.30, {'x': 40,  'rotation': -10}),
            (0.45, {'x': -40, 'rotation': 10}),
        ],
        'particles': None,
        'duration': 0.6,
    },
    Reaction.THINKING: {
        'impulse': {'rotation': -15, 'x': -20},
        'expression': 'thinking',
        'particles': None,
        'duration': 1.2,
    },
    Reaction.SAD: {
        'impulse': {'y': 40},
        'expression': 'sad',
        'particles': None,
        'duration': 1.5,
    },
    Reaction.CELEBRATE: {
        'impulse': {'y': -150, 'scale': 0.18, 'rotation': 12},
        'expression': 'excited',
        'particles': 'confetti',
        'duration': 0.5,
    },
    Reaction.RAID: {
        'impulse': {'y': -200, 'scale': 0.25, 'rotation': 15},
        'expression': 'excited',
        'particles': 'confetti_big',
        'duration': 0.4,
    },
    Reaction.SUB: {
        'impulse': {'y': -100, 'scale': 0.12},
        'expression': 'happy',
        'particles': 'hearts',
        'duration': 0.5,
    },
    Reaction.FOLLOW: {
        'impulse': {'y': -50, 'x': 30},
        'expression': 'happy',
        'particles': 'hearts_small',
        'duration': 0.4,
    },
    Reaction.DONATION: {
        'impulse': {'y': -140, 'scale': 0.2, 'rotation': -10},
        'expression': 'surprised',
        'particles': 'coins',
        'duration': 0.4,
    },
    Reaction.BITS: {
        'impulse': {'y': -70, 'scale': 0.10},
        'expression': 'excited',
        'particles': 'stars',
        'duration': 0.4,
    },
    Reaction.TILT_LEFT: {
        'impulse': {'rotation': -12},
        'expression': 'neutral',
        'particles': None,
        'duration': 0.2,
    },
    Reaction.TILT_RIGHT: {
        'impulse': {'rotation': 12},
        'expression': 'neutral',
        'particles': None,
        'duration': 0.2,
    },
}


class AnimationController:
    """
    High-level animation API.  Call trigger(reaction) and then
    apply(avatar_rgba, frame_bgr) each video frame.
    """

    def __init__(self, canvas_width: int = 1280, canvas_height: int = 720):
        self.physics    = AvatarPhysics()
        self.expression = ExpressionLayer()
        self.particles  = ParticleSystem()
        self.canvas_w   = canvas_width
        self.canvas_h   = canvas_height
        self._scheduled: list[tuple[float, dict]] = []
        self._lock      = threading.Lock()

    # ------------------------------------------------------------------
    # Trigger a named reaction
    # ------------------------------------------------------------------

    def trigger(self, reaction: Reaction):
        script = _REACTION_SCRIPTS.get(reaction, _REACTION_SCRIPTS[Reaction.IDLE])
        now = time.perf_counter()

        imp = script.get('impulse', {})
        if imp:
            self.physics.impulse(**imp)

        expr = script.get('expression', 'neutral')
        self.expression.set(expr)

        # Reset targets after duration
        dur = script.get('duration', 0)
        if dur > 0:
            with self._lock:
                self._scheduled.append((now + dur, {'reset': True}))

        # Scheduled secondary impulses (e.g. laugh shake sequence)
        for delay, extra_imp in script.get('sequence', []):
            with self._lock:
                self._scheduled.append((now + delay, {'impulse': extra_imp}))

        # Particles
        ptag = script.get('particles')
        if ptag:
            cx = self.canvas_w // 2
            cy = self.canvas_h // 3
            if ptag == 'confetti':
                self.particles.burst_confetti(cx, cy, 70)
            elif ptag == 'confetti_big':
                self.particles.burst_confetti(cx, cy, 150)
            elif ptag in ('hearts', 'hearts_small'):
                count = 25 if ptag == 'hearts' else 12
                self.particles.burst_hearts(cx, cy, count)
            elif ptag == 'stars':
                self.particles.burst_stars(cx, cy, 30)
            elif ptag == 'coins':
                self.particles.burst_coins(cx, cy, 30)

    # ------------------------------------------------------------------
    # Per-frame update and render
    # ------------------------------------------------------------------

    def apply(self, avatar_rgba: np.ndarray,
              output_frame: np.ndarray) -> tuple[np.ndarray, Transform]:
        """
        1. Process scheduled events
        2. Apply expression to avatar
        3. Return (animated_avatar_rgba, current_transform)

        The caller (compositor) uses the Transform to position/scale/rotate
        the avatar when blending it onto output_frame.
        Particles are drawn directly onto output_frame.
        """
        self._process_scheduled()

        # Expression
        animated = self.expression.apply(avatar_rgba)

        # Physics transform
        t = self.physics.update()

        # Draw particles onto the output frame
        output_frame[:] = self.particles.update_and_draw(output_frame)

        return animated, t

    # ------------------------------------------------------------------

    def _process_scheduled(self):
        now = time.perf_counter()
        with self._lock:
            remaining = []
            for trigger_t, action in self._scheduled:
                if now >= trigger_t:
                    if action.get('reset'):
                        self.physics.reset_targets()
                        self.expression.set('neutral')
                    elif 'impulse' in action:
                        self.physics.impulse(**action['impulse'])
                else:
                    remaining.append((trigger_t, action))
            self._scheduled = remaining
