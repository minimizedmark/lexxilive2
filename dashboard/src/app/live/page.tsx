'use client';

import { useEffect, useState, useCallback } from 'react';
import { supabase } from '@/lib/supabase';
import { getSessions, getCreators, startSession, endSession, sendCommand } from '@/lib/api';
import EmotionBadge from '@/components/EmotionBadge';
import EventFeed from '@/components/EventFeed';
import type { Creator, StreamSession } from '@/lib/supabase';

export default function LivePage() {
  const [sessions,       setSessions]       = useState<StreamSession[]>([]);
  const [creators,       setCreators]       = useState<Creator[]>([]);
  const [activeSession,  setActiveSession]  = useState<StreamSession | null>(null);
  const [loading,        setLoading]        = useState(true);
  const [error,          setError]          = useState('');
  const [startingSlug,   setStartingSlug]   = useState('');
  const [starting,       setStarting]       = useState(false);
  const [injectText,     setInjectText]     = useState('');
  const [injectUser,     setInjectUser]     = useState('');

  const load = useCallback(async () => {
    try {
      const [sess, creat] = await Promise.all([
        getSessions() as Promise<StreamSession[]>,
        getCreators() as Promise<Creator[]>,
      ]);
      setSessions(sess);
      setCreators(creat);
      const live = sess.find((s) => s.status === 'live') ?? null;
      setActiveSession(live);
      if (live && !startingSlug) setStartingSlug('');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, [startingSlug]);

  useEffect(() => { load(); }, [load]);

  // Realtime: subscribe to updates on stream_sessions
  useEffect(() => {
    const channel = supabase
      .channel('live_session_updates')
      .on('postgres_changes', { event: 'UPDATE', schema: 'public', table: 'stream_sessions' },
        (payload) => {
          const updated = payload.new as StreamSession;
          setSessions((prev) => prev.map((s) => s.id === updated.id ? updated : s));
          setActiveSession((prev) => prev?.id === updated.id ? updated : prev);
        })
      .subscribe();
    return () => { supabase.removeChannel(channel); };
  }, []);

  async function handleStart() {
    setStarting(true);
    setError('');
    try {
      const session = await startSession({ creator_id: startingSlug || null }) as StreamSession;
      setActiveSession(session);
      setSessions((prev) => [session, ...prev]);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to start session');
    } finally {
      setStarting(false);
    }
  }

  async function handleEnd() {
    if (!activeSession) return;
    if (!confirm('End this session?')) return;
    try {
      await endSession(activeSession.id);
      setActiveSession(null);
      load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to end session');
    }
  }

  async function handleSwitchCreator(slug: string) {
    try {
      await sendCommand({ action: 'switch_creator', payload: { slug }, session_id: activeSession?.id });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Command failed');
    }
  }

  async function handleInjectEvent(e: React.FormEvent) {
    e.preventDefault();
    if (!injectText.trim() || !activeSession) return;
    try {
      await sendCommand({
        action: 'inject_event',
        payload: { event: { event_type: 'chat_message', user_name: injectUser || 'dashboard', message: injectText } },
        session_id: activeSession.id,
      });
      setInjectText('');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Inject failed');
    }
  }

  async function handleSetMode(mode: string) {
    try {
      await sendCommand({ action: 'set_mode', payload: { mode }, session_id: activeSession?.id });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Command failed');
    }
  }

  if (loading) return <p className="text-gray-500">Loading…</p>;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <h1 className="text-2xl font-bold">Live Control</h1>

      {error && (
        <div className="rounded-lg border border-red-700 bg-red-900/30 p-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* Session controls */}
      <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
        <h2 className="mb-4 font-semibold text-gray-200">Session</h2>
        {activeSession ? (
          <div className="flex flex-wrap items-start gap-4">
            <div className="flex-1 space-y-1">
              <div className="flex items-center gap-3">
                <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
                <span className="font-semibold text-red-400">LIVE</span>
                <span className="text-xs text-gray-500 font-mono">{activeSession.id.slice(0, 8)}…</span>
              </div>
              <p className="text-sm text-gray-400">
                Started {new Date(activeSession.started_at).toLocaleTimeString()} ·{' '}
                {activeSession.total_events} events
              </p>
              <div className="flex items-center gap-2">
                <span className="text-sm text-gray-500">State:</span>
                <span className="rounded bg-gray-800 px-2 py-0.5 text-xs font-mono text-gray-300">
                  {activeSession.current_state}
                </span>
                <EmotionBadge emotion={activeSession.current_emotion} />
              </div>
            </div>
            <button
              onClick={handleEnd}
              className="rounded-lg border border-red-800 px-4 py-2 text-sm text-red-400 hover:bg-red-900/30 transition-colors"
            >
              End Session
            </button>
          </div>
        ) : (
          <div className="flex flex-wrap gap-3">
            <select
              value={startingSlug}
              onChange={(e) => setStartingSlug(e.target.value)}
              className="rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200 focus:border-brand-500 focus:outline-none"
            >
              <option value="">No specific creator</option>
              {creators.map((c) => (
                <option key={c.id} value={c.slug}>{c.name} (@{c.slug})</option>
              ))}
            </select>
            <button
              onClick={handleStart}
              disabled={starting}
              className="rounded-lg bg-brand-600 px-5 py-2 text-sm font-semibold hover:bg-brand-700 disabled:opacity-50 transition-colors"
            >
              {starting ? 'Starting…' : 'Start Session'}
            </button>
          </div>
        )}
      </div>

      {activeSession && (
        <>
          {/* Creator switcher */}
          <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
            <h2 className="mb-3 font-semibold text-gray-200">Switch Creator</h2>
            <div className="flex flex-wrap gap-2">
              {creators.map((c) => (
                <button
                  key={c.id}
                  onClick={() => handleSwitchCreator(c.slug)}
                  className="rounded-lg border border-gray-700 px-4 py-1.5 text-sm hover:border-brand-500 hover:bg-brand-600/10 transition-colors"
                >
                  {c.name}
                </button>
              ))}
              {creators.length === 0 && (
                <p className="text-sm text-gray-500">No creators configured.</p>
              )}
            </div>
          </div>

          {/* Mode setter */}
          <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
            <h2 className="mb-3 font-semibold text-gray-200">Set Mode</h2>
            <div className="flex flex-wrap gap-2">
              {['hype', 'chill', 'focus', 'roast', 'wholesome'].map((mode) => (
                <button
                  key={mode}
                  onClick={() => handleSetMode(mode)}
                  className="rounded-lg border border-gray-700 px-4 py-1.5 text-sm capitalize hover:border-brand-500 hover:bg-brand-600/10 transition-colors"
                >
                  {mode}
                </button>
              ))}
            </div>
          </div>

          {/* Inject chat event */}
          <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
            <h2 className="mb-3 font-semibold text-gray-200">Inject Chat Event</h2>
            <form onSubmit={handleInjectEvent} className="flex gap-2">
              <input
                value={injectUser}
                onChange={(e) => setInjectUser(e.target.value)}
                placeholder="Username"
                className="w-36 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 placeholder-gray-600 focus:border-brand-500 focus:outline-none"
              />
              <input
                value={injectText}
                onChange={(e) => setInjectText(e.target.value)}
                placeholder="Message to inject…"
                required
                className="flex-1 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 placeholder-gray-600 focus:border-brand-500 focus:outline-none"
              />
              <button
                type="submit"
                className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold hover:bg-brand-700 transition-colors"
              >
                Send
              </button>
            </form>
          </div>

          {/* Event feed */}
          <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
            <h2 className="mb-3 font-semibold text-gray-200">Event Feed</h2>
            <EventFeed sessionId={activeSession.id} />
          </div>
        </>
      )}

      {/* Past sessions */}
      {sessions.filter((s) => s.status !== 'live').length > 0 && (
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
          <h2 className="mb-3 font-semibold text-gray-200">Past Sessions</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800 text-left text-xs text-gray-500">
                  <th className="pb-2 pr-4">Started</th>
                  <th className="pb-2 pr-4">Duration</th>
                  <th className="pb-2 pr-4">Events</th>
                  <th className="pb-2 pr-4">Status</th>
                </tr>
              </thead>
              <tbody>
                {sessions.filter((s) => s.status !== 'live').slice(0, 10).map((s) => (
                  <tr key={s.id} className="border-b border-gray-800/50 text-gray-400">
                    <td className="py-2 pr-4">{new Date(s.started_at).toLocaleString()}</td>
                    <td className="py-2 pr-4">{duration(s)}</td>
                    <td className="py-2 pr-4">{s.total_events}</td>
                    <td className="py-2 pr-4">
                      <span className="rounded-full bg-gray-800 px-2 py-0.5 text-xs">{s.status}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function duration(s: StreamSession): string {
  const end   = s.ended_at ? new Date(s.ended_at) : new Date();
  const start = new Date(s.started_at);
  const secs  = Math.floor((end.getTime() - start.getTime()) / 1000);
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const sec = secs % 60;
  return h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}
