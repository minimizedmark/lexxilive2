"""
Physical hardware integration — real-world devices that react to
the AI persona's emotional state and stream events in real time.

Supported:
  PhilipsHueDriver      – Hue Bridge (phue library)
  WLEDDriver            – WLED HTTP API (ESP32/8266 LED strips)
  GoveeDriver           – Govee BLE lights (govee-api-laggat or direct BLE)
  ArduinoSerialDriver   – Arduino/serial devices (custom LED/servo rigs)
  HomeAssistantDriver   – Home Assistant webhook
  ElgatoKeyLightDriver  – Elgato Key Light / Key Light Air (HTTP)

All drivers implement the LightDriver interface:
  set_color(r, g, b, brightness=1.0)
  set_scene(scene_name)
  pulse(r, g, b, duration_s)
  off()

HardwareManager:
  Holds all configured drivers and maps emotional state → lighting color.
  Emotion color palette follows standard affect theory:
    excited/happy  → warm gold / amber
    calm/content   → soft blue / teal
    surprised      → white flash
    sad            → dim blue-purple
    tense/angry    → red
    thinking       → cool white
    neutral        → soft white
"""

import threading
import time
import math
from abc import ABC, abstractmethod
from typing import Iterable


# ---------------------------------------------------------------------------
# Emotion → color mapping
# ---------------------------------------------------------------------------

EMOTION_COLORS: dict[str, tuple[int, int, int]] = {
    'excited':   (255, 160,  20),   # warm amber
    'happy':     (255, 200,  60),   # golden yellow
    'content':   (100, 220, 180),   # teal
    'calm':      ( 80, 130, 255),   # cool blue
    'curious':   (180, 100, 255),   # purple-blue
    'thinking':  (200, 200, 255),   # cold white-blue
    'surprised': (255, 255, 255),   # white flash
    'tense':     (255,  60,  40),   # red-orange
    'worried':   (200, 100,  40),   # amber-brown
    'sad':       ( 60,  60, 180),   # dim blue-indigo
    'displeased':( 180, 60,  60),   # muted red
    'neutral':   (220, 200, 170),   # soft warm white
}

def emotion_color(label: str) -> tuple[int, int, int]:
    return EMOTION_COLORS.get(label, EMOTION_COLORS['neutral'])


# ---------------------------------------------------------------------------
# Base driver
# ---------------------------------------------------------------------------

class LightDriver(ABC):
    name: str = 'base'

    @abstractmethod
    def set_color(self, r: int, g: int, b: int, brightness: float = 1.0): ...

    @abstractmethod
    def off(self): ...

    def set_scene(self, scene: str): pass

    def pulse(self, r: int, g: int, b: int,
              duration_s: float = 0.4, count: int = 2):
        """Quick brightness pulse for event reactions."""
        def _pulse():
            for _ in range(count):
                self.set_color(r, g, b, brightness=1.0)
                time.sleep(duration_s / (count * 2))
                self.set_color(r, g, b, brightness=0.3)
                time.sleep(duration_s / (count * 2))
            self.set_color(r, g, b, brightness=0.8)
        threading.Thread(target=_pulse, daemon=True).start()

    def transition_to(self, r: int, g: int, b: int,
                      duration_s: float = 1.0, steps: int = 30,
                      current_rgb: tuple = (220, 200, 170)):
        """Smooth color transition."""
        cr, cg, cb = current_rgb
        def _trans():
            for i in range(1, steps + 1):
                t = i / steps
                nr = int(cr + (r - cr) * t)
                ng = int(cg + (g - cg) * t)
                nb = int(cb + (b - cb) * t)
                self.set_color(nr, ng, nb)
                time.sleep(duration_s / steps)
        threading.Thread(target=_trans, daemon=True).start()


# ---------------------------------------------------------------------------
# Philips Hue
# ---------------------------------------------------------------------------

class PhilipsHueDriver(LightDriver):
    name = 'hue'

    def __init__(self, bridge_ip: str, light_names: list[str] | None = None):
        try:
            from phue import Bridge
            self._bridge = Bridge(bridge_ip)
            self._bridge.connect()
            lights = self._bridge.get_light_objects('name')
            self._lights = (
                [lights[n] for n in light_names if n in lights]
                if light_names else list(lights.values())
            )
            print(f"[HW/Hue] Connected: {len(self._lights)} light(s)")
        except ImportError:
            print("[HW/Hue] phue not installed.  pip install phue")
            self._lights = []
        except Exception as e:
            print(f"[HW/Hue] Connection failed: {e}")
            self._lights = []

    def set_color(self, r: int, g: int, b: int, brightness: float = 1.0):
        xy = _rgb_to_hue_xy(r, g, b)
        bri = int(brightness * 254)
        for light in self._lights:
            try:
                light.xy = xy
                light.brightness = bri
                light.on = True
            except Exception:
                pass

    def off(self):
        for light in self._lights:
            try:
                light.on = False
            except Exception:
                pass


# ---------------------------------------------------------------------------
# WLED  (ESP32/ESP8266 LED controller)
# ---------------------------------------------------------------------------

class WLEDDriver(LightDriver):
    name = 'wled'

    def __init__(self, host: str, port: int = 80):
        self._url = f'http://{host}:{port}/json/state'
        self._current = (220, 200, 170)
        try:
            import requests
            r = requests.get(f'http://{host}:{port}/json/info', timeout=3)
            r.raise_for_status()
            info = r.json()
            print(f"[HW/WLED] Connected: {info.get('name', host)}")
        except Exception as e:
            print(f"[HW/WLED] Could not reach {host}: {e}")

    def set_color(self, r: int, g: int, b: int, brightness: float = 1.0):
        try:
            import requests
            bri = int(brightness * 255)
            requests.post(self._url, json={
                'on': True,
                'bri': bri,
                'seg': [{'col': [[r, g, b]]}],
            }, timeout=2)
            self._current = (r, g, b)
        except Exception as e:
            pass   # non-blocking; hardware errors are silent

    def set_scene(self, scene: str):
        """WLED preset by name (must be saved on the device)."""
        PRESETS = {'party': 1, 'chill': 2, 'alert': 3}
        pid = PRESETS.get(scene.lower(), 0)
        if pid:
            try:
                import requests
                requests.post(self._url, json={'ps': pid}, timeout=2)
            except Exception:
                pass

    def off(self):
        try:
            import requests
            requests.post(self._url, json={'on': False}, timeout=2)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Govee  (BLE or LAN API)
# ---------------------------------------------------------------------------

class GoveeDriver(LightDriver):
    name = 'govee'

    def __init__(self, api_key: str, device_id: str, device_model: str):
        import os
        self._key   = api_key or os.environ.get('GOVEE_API_KEY', '')
        self._dev   = device_id
        self._model = device_model
        if not self._key:
            print("[HW/Govee] GOVEE_API_KEY not set.")

    def set_color(self, r: int, g: int, b: int, brightness: float = 1.0):
        if not self._key:
            return
        try:
            import requests
            bri = int(brightness * 100)
            requests.put('https://developer-api.govee.com/v1/devices/control',
                headers={'Govee-API-Key': self._key},
                json={
                    'device': self._dev, 'model': self._model,
                    'cmd': {'name': 'color', 'value': {'r': r, 'g': g, 'b': b}},
                }, timeout=5)
            requests.put('https://developer-api.govee.com/v1/devices/control',
                headers={'Govee-API-Key': self._key},
                json={
                    'device': self._dev, 'model': self._model,
                    'cmd': {'name': 'brightness', 'value': bri},
                }, timeout=5)
        except Exception as e:
            pass

    def off(self):
        if not self._key:
            return
        try:
            import requests
            requests.put('https://developer-api.govee.com/v1/devices/control',
                headers={'Govee-API-Key': self._key},
                json={'device': self._dev, 'model': self._model,
                      'cmd': {'name': 'turn', 'value': 'off'}},
                timeout=5)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Arduino / Serial
# ---------------------------------------------------------------------------

class ArduinoSerialDriver(LightDriver):
    """
    Sends RGB commands over serial to an Arduino.
    Expected protocol: 'RGB r g g\\n'  (values 0-255)
    Sketch example:
        void loop() {
          if (Serial.available()) {
            String cmd = Serial.readStringUntil('\\n');
            if (cmd.startsWith("RGB")) {
              int r = cmd.substring(4).toInt();
              // parse g, b ...
              setLEDs(r, g, b);
            }
          }
        }
    """
    name = 'arduino'

    def __init__(self, port: str, baud: int = 115200):
        try:
            import serial as pyserial
            self._ser = pyserial.Serial(port, baud, timeout=1)
            time.sleep(2)   # Arduino reset
            print(f"[HW/Arduino] Connected on {port}")
        except ImportError:
            print("[HW/Arduino] pyserial not installed.  pip install pyserial")
            self._ser = None
        except Exception as e:
            print(f"[HW/Arduino] Could not open {port}: {e}")
            self._ser = None

    def set_color(self, r: int, g: int, b: int, brightness: float = 1.0):
        if self._ser is None:
            return
        r2 = int(r * brightness)
        g2 = int(g * brightness)
        b2 = int(b * brightness)
        try:
            self._ser.write(f'RGB {r2} {g2} {b2}\n'.encode())
        except Exception:
            pass

    def off(self):
        self.set_color(0, 0, 0)


# ---------------------------------------------------------------------------
# Home Assistant webhook
# ---------------------------------------------------------------------------

class HomeAssistantDriver(LightDriver):
    """
    Fires Home Assistant webhooks on color / scene changes.
    Configure automations in HA triggered by webhook IDs.
    """
    name = 'home-assistant'

    def __init__(self, base_url: str, webhook_id: str, token: str = ''):
        import os
        self._base = base_url.rstrip('/')
        self._wid  = webhook_id
        self._tok  = token or os.environ.get('HA_TOKEN', '')

    def set_color(self, r: int, g: int, b: int, brightness: float = 1.0):
        self._fire({'action': 'color', 'r': r, 'g': g, 'b': b,
                    'brightness': brightness})

    def set_scene(self, scene: str):
        self._fire({'action': 'scene', 'scene': scene})

    def off(self):
        self._fire({'action': 'off'})

    def _fire(self, payload: dict):
        try:
            import requests
            headers = {}
            if self._tok:
                headers['Authorization'] = f'Bearer {self._tok}'
            requests.post(
                f'{self._base}/api/webhook/{self._wid}',
                json=payload, headers=headers, timeout=3)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Elgato Key Light / Key Light Air
# ---------------------------------------------------------------------------

class ElgatoKeyLightDriver(LightDriver):
    """
    Controls Elgato Key Lights over local network (UDP discovery or fixed IP).
    Key Lights only support brightness + color temperature, not RGB.
    """
    name = 'elgato-keylight'

    def __init__(self, host: str, port: int = 9123):
        self._url = f'http://{host}:{port}/elgato/lights'
        try:
            import requests
            r = requests.get(self._url, timeout=3)
            r.raise_for_status()
            print(f"[HW/Elgato] Connected: {host}")
        except Exception as e:
            print(f"[HW/Elgato] Could not reach {host}: {e}")

    def set_color(self, r: int, g: int, b: int, brightness: float = 1.0):
        # Convert RGB to color temperature (Kelvin approx)
        kelvin = _rgb_to_kelvin(r, g, b)
        bri = int(brightness * 100)
        self._set(bri, kelvin)

    def off(self):
        try:
            import requests
            requests.put(self._url,
                json={'lights': [{'on': 0}]}, timeout=3)
        except Exception:
            pass

    def _set(self, brightness_pct: int, kelvin: int):
        try:
            import requests
            # Elgato uses 2900–7000 K; map 143-344 (their unit)
            kel_unit = max(143, min(344, int(1000000 / max(1, kelvin))))
            requests.put(self._url, json={
                'lights': [{'on': 1, 'brightness': brightness_pct,
                             'temperature': kel_unit}]
            }, timeout=3)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Hardware manager (orchestrator)
# ---------------------------------------------------------------------------

class HardwareManager:
    """
    Manages all light drivers and reacts to emotional state changes.

    Usage:
        hw = HardwareManager()
        hw.add(WLEDDriver('192.168.1.50'))
        hw.add(PhilipsHueDriver('192.168.1.2'))

        # Called by ReactionEngine on state change:
        hw.on_emotion(state)

        # Called on stream events:
        hw.on_event('raid')
    """

    def __init__(self):
        self._drivers: list[LightDriver] = []
        self._current_rgb = (220, 200, 170)
        self._lock = threading.Lock()

    def add(self, driver: LightDriver):
        self._drivers.append(driver)
        print(f"[HW] Registered: {driver.name}")

    # ------------------------------------------------------------------

    def on_emotion(self, state):
        """Called by ReactionEngine when emotional label changes."""
        r, g, b = emotion_color(state.label)
        # Scale brightness by arousal: calm = 60%, excited = 100%
        brightness = 0.6 + (state.arousal + 1) / 2 * 0.4
        self._set_all(r, g, b, brightness, transition=1.5)

    def on_event(self, event_type: str):
        """Called on stream events for immediate reaction flashes."""
        pulses = {
            'raid':     ((255, 80,  30), 0.3, 4),
            'sub':      ((255, 160, 200), 0.25, 3),
            'gifted':   ((180, 100, 255), 0.2, 3),
            'donation': ((255, 220,  40), 0.2, 3),
            'bits':     ((100, 200, 255), 0.2, 2),
            'follow':   ((255, 200, 160), 0.3, 2),
        }
        entry = pulses.get(event_type.lower())
        if entry:
            (r, g, b), dur, count = entry
            self._pulse_all(r, g, b, dur, count)

    def off_all(self):
        for d in self._drivers:
            try:
                d.off()
            except Exception:
                pass

    # ------------------------------------------------------------------

    def _set_all(self, r, g, b, brightness=1.0, transition=0.0):
        prev = self._current_rgb
        self._current_rgb = (r, g, b)
        for d in self._drivers:
            try:
                if transition > 0:
                    d.transition_to(r, g, b, duration_s=transition,
                                    current_rgb=prev)
                else:
                    d.set_color(r, g, b, brightness)
            except Exception:
                pass

    def _pulse_all(self, r, g, b, dur, count):
        for d in self._drivers:
            try:
                d.pulse(r, g, b, duration_s=dur, count=count)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Color conversion helpers
# ---------------------------------------------------------------------------

def _rgb_to_hue_xy(r: int, g: int, b: int) -> list[float]:
    """Convert RGB to Philips Hue CIE xy."""
    r2 = _gamma(r / 255)
    g2 = _gamma(g / 255)
    b2 = _gamma(b / 255)
    X = r2 * 0.664511 + g2 * 0.154324 + b2 * 0.162028
    Y = r2 * 0.283881 + g2 * 0.668433 + b2 * 0.047685
    Z = r2 * 0.000088 + g2 * 0.072310 + b2 * 0.986039
    d = X + Y + Z
    if d == 0:
        return [0.3127, 0.3290]
    return [round(X / d, 4), round(Y / d, 4)]

def _gamma(v: float) -> float:
    return pow((v + 0.055) / 1.055, 2.4) if v > 0.04045 else v / 12.92

def _rgb_to_kelvin(r: int, g: int, b: int) -> int:
    """Rough RGB → color temperature conversion."""
    if r == 0 and g == 0:
        return 2700
    ratio = r / max(1, b)
    k = int(6600 / max(0.1, ratio))
    return max(2700, min(7000, k))
