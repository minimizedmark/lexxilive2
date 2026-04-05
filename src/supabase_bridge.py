"""
supabase_bridge.py — Python WebSocket client that connects the stream engine
to the Node.js backend for persistent event logging and real-time commands.

Usage:

    bridge = SupabaseBridge(ws_url='ws://localhost:3000/ws', session_id='<uuid>')

    # Register a handler for dashboard commands before starting.
    bridge.on_command(lambda cmd: handle_command(cmd))

    bridge.start()

    # Report a stream event (fire-and-forget; queued if not connected):
    bridge.report_event({
        'event_type': 'subscription',
        'user_name':  'Alice',
        'message':    '',
        'amount':     1,
        'metadata':   {'months': 3},
    })

    # Report current AI state periodically:
    bridge.report_state(
        state_label='talking',
        emotion_valence=0.8,
        arousal=0.6,
        creator_slug='lexi',
    )

    bridge.stop()

The bridge runs in a background daemon thread.  All public methods are
thread-safe and non-blocking.
"""

import json
import logging
import queue
import threading
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)

# Try to import websocket-client; fall back gracefully if not installed.
try:
    import websocket  # websocket-client package
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False
    log.warning("[SupabaseBridge] 'websocket-client' not installed. "
                "Run: pip install websocket-client")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BACKOFF_INITIAL  = 1.0    # seconds before first reconnect attempt
_BACKOFF_MAX      = 60.0   # maximum back-off cap
_BACKOFF_FACTOR   = 2.0    # exponential multiplier
_SEND_QUEUE_SIZE  = 512    # max buffered outgoing messages


class SupabaseBridge:
    """
    WebSocket client bridge between the Python stream engine and the
    Node.js backend.

    Parameters
    ----------
    ws_url      : WebSocket URL, e.g. 'ws://localhost:3000/ws'
    session_id  : UUID of the current stream session (set after POST /api/sessions)
    """

    def __init__(self, ws_url: str, session_id: Optional[str] = None):
        self._ws_url     = ws_url
        self._session_id = session_id

        self._ws: Optional['websocket.WebSocketApp'] = None
        self._thread: Optional[threading.Thread]     = None
        self._running   = False
        self._connected = False

        # Outgoing message queue — filled by report_* helpers, drained by the
        # background sender loop once connected.
        self._send_q: queue.Queue[str] = queue.Queue(maxsize=_SEND_QUEUE_SIZE)

        # Registered command callback
        self._command_cb: Optional[Callable[[dict], None]] = None

        # Reconnect state
        self._backoff = _BACKOFF_INITIAL

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    @session_id.setter
    def session_id(self, value: str):
        """Update the session_id after a new session has been created."""
        self._session_id = value
        # Re-send hello so the server updates its session map.
        if self._connected:
            self._send_hello()

    def on_command(self, callback: Callable[[dict], None]):
        """
        Register a handler for commands sent from the Node dashboard.

        The callback receives the full parsed command dict, e.g.:
            { 'type': 'command', 'action': 'switch_creator', 'slug': 'lexi' }
            { 'type': 'command', 'action': 'inject_event', 'event': {...} }
            { 'type': 'command', 'action': 'set_mode', 'mode': 'hype' }

        The callback is called from the bridge background thread.
        """
        self._command_cb = callback

    def start(self):
        """Start the background WebSocket thread.  Idempotent."""
        if not _WS_AVAILABLE:
            log.error("[SupabaseBridge] Cannot start: websocket-client not installed.")
            return
        if self._running:
            return

        self._running = True
        self._thread  = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name='supabase-bridge',
        )
        self._thread.start()
        log.info("[SupabaseBridge] Background thread started → %s", self._ws_url)

    def stop(self):
        """Gracefully stop the bridge."""
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)
        log.info("[SupabaseBridge] Stopped.")

    def report_event(self, event_dict: dict):
        """
        Queue a stream event for logging to Supabase.

        event_dict keys (all optional except event_type):
            event_type  : str   — e.g. 'chat_message', 'subscription', 'raid'
            user_name   : str
            message     : str
            amount      : int   — bits / donation cents / gifted-sub count
            metadata    : dict
        """
        payload = {
            'session_id': self._session_id,
            **event_dict,
        }
        self._enqueue({'type': 'event', 'payload': payload})

    def report_state(
        self,
        state_label: str,
        emotion_valence: float = 0.0,
        arousal: float = 0.0,
        creator_slug: Optional[str] = None,
    ):
        """
        Send a state snapshot to the backend so the dashboard stays in sync.

        Parameters
        ----------
        state_label     : Human-readable state string, e.g. 'talking', 'idle'
        emotion_valence : Float in [-1, 1] — negative = sad, positive = happy
        arousal         : Float in [0, 1]  — 0 = calm, 1 = excited
        creator_slug    : Slug of the currently active creator (or None)
        """
        payload: dict = {
            'state': state_label,
            'emotion': {
                'valence': round(float(emotion_valence), 3),
                'arousal': round(float(arousal), 3),
            },
        }
        if creator_slug:
            payload['creator'] = creator_slug

        self._enqueue({'type': 'state', 'payload': payload})

    # ------------------------------------------------------------------
    # Internal — connection lifecycle
    # ------------------------------------------------------------------

    def _run_loop(self):
        """Background reconnect loop with exponential back-off."""
        while self._running:
            try:
                self._connect()
            except Exception as exc:
                log.warning("[SupabaseBridge] Connection error: %s", exc)

            if not self._running:
                break

            log.info("[SupabaseBridge] Reconnecting in %.1fs...", self._backoff)
            time.sleep(self._backoff)
            self._backoff = min(self._backoff * _BACKOFF_FACTOR, _BACKOFF_MAX)

    def _connect(self):
        """Open a WebSocket connection and block until it closes."""
        self._ws = websocket.WebSocketApp(
            self._ws_url,
            on_open    = self._on_open,
            on_message = self._on_message,
            on_error   = self._on_error,
            on_close   = self._on_close,
        )
        # run_forever blocks until the connection is closed or an error occurs.
        self._ws.run_forever(ping_interval=30, ping_timeout=10)

    # ------------------------------------------------------------------
    # WebSocket callbacks (called by websocket-client from bridge thread)
    # ------------------------------------------------------------------

    def _on_open(self, ws):
        self._connected = True
        self._backoff   = _BACKOFF_INITIAL   # reset on successful connect
        log.info("[SupabaseBridge] Connected to %s", self._ws_url)

        # Identify ourselves to the server so it can route commands.
        self._send_hello()

        # Drain any messages that were queued while disconnected.
        self._flush_queue()

    def _on_message(self, ws, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("[SupabaseBridge] Non-JSON message received, ignoring.")
            return

        if msg.get('type') == 'command':
            self._dispatch_command(msg)
        else:
            log.debug("[SupabaseBridge] Unhandled inbound message type: %s", msg.get('type'))

    def _on_error(self, ws, error):
        log.warning("[SupabaseBridge] WebSocket error: %s", error)

    def _on_close(self, ws, close_status_code, close_msg):
        self._connected = False
        log.info(
            "[SupabaseBridge] Connection closed (code=%s msg=%s)",
            close_status_code,
            close_msg,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _send_hello(self):
        """Send the hello handshake so the server knows our session_id."""
        msg = {'type': 'hello', 'payload': {'session_id': self._session_id}}
        self._send_raw(json.dumps(msg))

    def _enqueue(self, msg: dict):
        """Thread-safe: add a message to the outgoing queue."""
        serialised = json.dumps(msg)
        try:
            self._send_q.put_nowait(serialised)
        except queue.Full:
            log.warning("[SupabaseBridge] Send queue full, dropping message.")
            return

        # If already connected, flush immediately.
        if self._connected:
            self._flush_queue()

    def _flush_queue(self):
        """Drain the outgoing queue over the live WebSocket connection."""
        while True:
            try:
                serialised = self._send_q.get_nowait()
            except queue.Empty:
                break
            if not self._send_raw(serialised):
                # Put it back and stop — connection dropped mid-flush.
                try:
                    self._send_q.put_nowait(serialised)
                except queue.Full:
                    pass
                break

    def _send_raw(self, data: str) -> bool:
        """
        Send a raw JSON string over the WebSocket.
        Returns True on success, False if the socket is unavailable.
        """
        if not self._ws or not self._connected:
            return False
        try:
            self._ws.send(data)
            return True
        except Exception as exc:
            log.warning("[SupabaseBridge] Send failed: %s", exc)
            self._connected = False
            return False

    def _dispatch_command(self, cmd: dict):
        """
        Forward a command from the dashboard to the registered callback.
        Called from the bridge thread — the callback must be thread-safe.
        """
        action = cmd.get('action', '<unknown>')
        log.info("[SupabaseBridge] Received command: action=%s", action)

        if self._command_cb is not None:
            try:
                self._command_cb(cmd)
            except Exception as exc:
                log.error("[SupabaseBridge] Command callback raised: %s", exc)
        else:
            log.warning("[SupabaseBridge] No command callback registered; dropping command.")
