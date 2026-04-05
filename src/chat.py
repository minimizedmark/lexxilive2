"""
Stream chat and event readers for Twitch and YouTube.

Each reader runs in its own daemon thread and pushes StreamEvent objects
to a shared queue consumed by the Brain.

Twitch:  uses IRC over WebSocket (no OAuth needed for read-only chat)
YouTube: polls the Live Chat API every ~5 s (requires YOUTUBE_API_KEY)

Usage:
    events = queue.Queue()
    tw = TwitchChatReader('channelname', events)
    tw.start()

    yt = YouTubeChatReader('LIVE_VIDEO_ID', events)
    yt.start()
"""

import time
import threading
import queue
import re
from .brain import StreamEvent, EventType


# ---------------------------------------------------------------------------
# Twitch IRC reader
# ---------------------------------------------------------------------------

class TwitchChatReader:
    """
    Connects to Twitch IRC (anonymous read-only) and pushes chat messages
    and channel point redemptions to the event queue.

    No OAuth token required for read-only chat.
    """

    IRC_HOST   = 'irc.chat.twitch.tv'
    IRC_PORT   = 6667
    NICK       = 'justinfan12345'   # anonymous read-only nick
    RECONNECT_DELAY = 5             # seconds between reconnect attempts

    def __init__(
        self,
        channel: str,
        event_queue: queue.Queue,
        filtered_words: list[str] | None = None,
        min_message_length: int = 3,
    ):
        self.channel          = channel.lower().lstrip('#')
        self.event_queue      = event_queue
        self.filtered_words   = [w.lower() for w in (filtered_words or [])]
        self.min_length       = min_message_length
        self._running         = False
        self._thread: threading.Thread | None = None
        self._sock            = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=f'twitch-{self.channel}')
        self._thread.start()
        print(f"[Twitch] Connecting to #{self.channel}…")

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass

    # ------------------------------------------------------------------

    def _run(self):
        while self._running:
            try:
                self._connect_and_read()
            except Exception as e:
                if self._running:
                    print(f"[Twitch] Disconnected ({e}). Reconnecting in "
                          f"{self.RECONNECT_DELAY}s…")
                    time.sleep(self.RECONNECT_DELAY)

    def _connect_and_read(self):
        import socket
        self._sock = socket.socket()
        self._sock.settimeout(300)
        self._sock.connect((self.IRC_HOST, self.IRC_PORT))
        self._send(f'NICK {self.NICK}')
        self._send(f'USER {self.NICK} 0 * :{self.NICK}')
        self._send('CAP REQ :twitch.tv/tags twitch.tv/commands')
        self._send(f'JOIN #{self.channel}')
        print(f"[Twitch] Joined #{self.channel}")

        buf = ''
        while self._running:
            chunk = self._sock.recv(4096).decode('utf-8', errors='replace')
            if not chunk:
                break
            buf += chunk
            while '\r\n' in buf:
                line, buf = buf.split('\r\n', 1)
                self._parse_line(line)

    def _send(self, msg: str):
        self._sock.sendall((msg + '\r\n').encode('utf-8'))

    def _parse_line(self, line: str):
        # PING keepalive
        if line.startswith('PING'):
            self._send('PONG ' + line[5:])
            return

        # Parse tags + command
        tags: dict = {}
        if line.startswith('@'):
            tag_str, line = line[1:].split(' ', 1)
            for part in tag_str.split(';'):
                if '=' in part:
                    k, v = part.split('=', 1)
                    tags[k] = v

        parts = line.split(' ', 3)
        if len(parts) < 3:
            return

        prefix  = parts[0]  # :user!user@user.tmi.twitch.tv
        command = parts[1]
        channel = parts[2] if len(parts) > 2 else ''
        message = parts[3].lstrip(':') if len(parts) > 3 else ''

        user = prefix.split('!')[0].lstrip(':') if '!' in prefix else prefix

        if command == 'PRIVMSG':
            self._handle_privmsg(user, message, tags)
        elif command == 'USERNOTICE':
            self._handle_usernotice(user, message, tags)

    def _handle_privmsg(self, user: str, message: str, tags: dict):
        # Filter bots, commands, spam
        if message.startswith('!'):
            return
        if len(message) < self.min_length:
            return
        if any(w in message.lower() for w in self.filtered_words):
            return

        self.event_queue.put_nowait(StreamEvent(
            type=EventType.CHAT_MESSAGE,
            user=tags.get('display-name', user),
            message=message.strip(),
        ))

    def _handle_usernotice(self, user: str, message: str, tags: dict):
        msg_id = tags.get('msg-id', '')
        display = tags.get('display-name', user)

        if msg_id in ('sub', 'resub'):
            months = int(tags.get('msg-param-cumulative-months', 1))
            self.event_queue.put_nowait(StreamEvent(
                type=EventType.SUBSCRIPTION,
                user=display,
                message=message,
                metadata={'months': months},
            ))

        elif msg_id == 'subgift':
            self.event_queue.put_nowait(StreamEvent(
                type=EventType.GIFTED_SUB,
                user=display,
                amount=1,
            ))

        elif msg_id == 'submysterygift':
            count = int(tags.get('msg-param-mass-gift-count', 1))
            self.event_queue.put_nowait(StreamEvent(
                type=EventType.GIFTED_SUB,
                user=display,
                amount=count,
            ))

        elif msg_id == 'raid':
            viewers = int(tags.get('msg-param-viewerCount', 0))
            self.event_queue.put_nowait(StreamEvent(
                type=EventType.RAID,
                user=display,
                amount=viewers,
            ))


# ---------------------------------------------------------------------------
# YouTube Live Chat reader
# ---------------------------------------------------------------------------

class YouTubeChatReader:
    """
    Polls the YouTube Live Chat API for new messages.
    Requires a YOUTUBE_API_KEY (Data API v3).

    Provide either:
      - live_video_id: the video ID of the ongoing livestream
      - channel_id:    auto-detects the active live video (slower)
    """

    POLL_INTERVAL = 5.0     # seconds between polls
    MAX_RESULTS   = 200

    def __init__(
        self,
        event_queue: queue.Queue,
        live_video_id: str = '',
        channel_id: str = '',
        api_key: str = '',
    ):
        import os
        self.event_queue  = event_queue
        self.video_id     = live_video_id
        self.channel_id   = channel_id
        self._key         = api_key or os.environ.get('YOUTUBE_API_KEY', '')
        self._chat_id     = ''
        self._page_token  = ''
        self._running     = False
        self._thread: threading.Thread | None = None

        if not self._key:
            print("[YouTube] WARNING: YOUTUBE_API_KEY not set. Chat disabled.")

    def start(self):
        if not self._key:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name='youtube-chat')
        self._thread.start()
        print("[YouTube] Chat reader starting…")

    def stop(self):
        self._running = False

    def _run(self):
        try:
            self._chat_id = self._resolve_chat_id()
        except Exception as e:
            print(f"[YouTube] Could not resolve live chat ID: {e}")
            return

        print(f"[YouTube] Monitoring live chat: {self._chat_id[:20]}…")
        while self._running:
            try:
                self._poll()
            except Exception as e:
                print(f"[YouTube] Poll error: {e}")
            time.sleep(self.POLL_INTERVAL)

    def _resolve_chat_id(self) -> str:
        import requests
        if self.video_id:
            url = 'https://www.googleapis.com/youtube/v3/videos'
            r = requests.get(url, params={
                'id': self.video_id, 'part': 'liveStreamingDetails', 'key': self._key
            }, timeout=10)
            r.raise_for_status()
            items = r.json().get('items', [])
            if items:
                return items[0]['liveStreamingDetails']['activeLiveChatId']

        if self.channel_id:
            url = 'https://www.googleapis.com/youtube/v3/search'
            r = requests.get(url, params={
                'channelId': self.channel_id,
                'type': 'video',
                'eventType': 'live',
                'part': 'id',
                'key': self._key,
            }, timeout=10)
            r.raise_for_status()
            items = r.json().get('items', [])
            if items:
                vid = items[0]['id']['videoId']
                return self._resolve_chat_id_from_video(vid)

        raise ValueError("No video_id or channel_id provided.")

    def _resolve_chat_id_from_video(self, vid: str) -> str:
        import requests
        url = 'https://www.googleapis.com/youtube/v3/videos'
        r = requests.get(url, params={
            'id': vid, 'part': 'liveStreamingDetails', 'key': self._key
        }, timeout=10)
        r.raise_for_status()
        items = r.json().get('items', [])
        return items[0]['liveStreamingDetails']['activeLiveChatId']

    def _poll(self):
        import requests
        params = {
            'liveChatId': self._chat_id,
            'part':       'snippet,authorDetails',
            'maxResults': self.MAX_RESULTS,
            'key':        self._key,
        }
        if self._page_token:
            params['pageToken'] = self._page_token

        r = requests.get(
            'https://www.googleapis.com/youtube/v3/liveChat/messages',
            params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        self._page_token = data.get('nextPageToken', '')

        for item in data.get('items', []):
            self._parse_item(item)

    def _parse_item(self, item: dict):
        snippet = item.get('snippet', {})
        author  = item.get('authorDetails', {})
        kind    = snippet.get('type', '')
        user    = author.get('displayName', 'viewer')

        if kind == 'textMessageEvent':
            msg = snippet.get('displayMessage', '').strip()
            if msg and not msg.startswith('!') and len(msg) >= 3:
                self.event_queue.put_nowait(StreamEvent(
                    type=EventType.CHAT_MESSAGE,
                    user=user,
                    message=msg,
                ))

        elif kind == 'memberMilestoneChatEvent':
            months = snippet.get('memberMilestoneChatEventDetails',
                                  {}).get('memberMonth', 1)
            self.event_queue.put_nowait(StreamEvent(
                type=EventType.SUBSCRIPTION,
                user=user,
                metadata={'months': months},
            ))

        elif kind == 'superChatEvent':
            details = snippet.get('superChatDetails', {})
            amount  = int(details.get('amountMicros', 0)) // 1_000_000
            comment = details.get('userComment', '')
            self.event_queue.put_nowait(StreamEvent(
                type=EventType.DONATION,
                user=user,
                message=comment,
                amount=amount,
            ))
