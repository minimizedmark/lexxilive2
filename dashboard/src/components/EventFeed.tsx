'use client';

import { useEffect, useRef, useState } from 'react';
import { supabase } from '@/lib/supabase';
import type { StreamEvent } from '@/lib/supabase';

const TYPE_STYLE: Record<string, string> = {
  chat_message: 'text-gray-300',
  subscription: 'text-brand-400 font-semibold',
  gifted_sub:   'text-purple-400 font-semibold',
  raid:         'text-orange-400 font-semibold',
  donation:     'text-green-400 font-semibold',
  bits:         'text-yellow-400 font-semibold',
  follow:       'text-teal-400',
};

const TYPE_ICON: Record<string, string> = {
  chat_message: '💬',
  subscription: '⭐',
  gifted_sub:   '🎁',
  raid:         '⚔️',
  donation:     '💰',
  bits:         '💎',
  follow:       '❤️',
};

export default function EventFeed({ sessionId }: { sessionId: string }) {
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Load recent events
  useEffect(() => {
    if (!sessionId) return;

    supabase
      .from('stream_events')
      .select('*')
      .eq('session_id', sessionId)
      .order('created_at', { ascending: false })
      .limit(100)
      .then(({ data }) => {
        if (data) setEvents(data.reverse() as StreamEvent[]);
      });

    // Real-time subscription for new events
    const channel = supabase
      .channel(`events_${sessionId}`)
      .on(
        'postgres_changes',
        {
          event:  'INSERT',
          schema: 'public',
          table:  'stream_events',
          filter: `session_id=eq.${sessionId}`,
        },
        (payload) => {
          setEvents((prev) => [...prev.slice(-199), payload.new as StreamEvent]);
        }
      )
      .subscribe();

    return () => { supabase.removeChannel(channel); };
  }, [sessionId]);

  // Auto-scroll to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events]);

  if (!sessionId) {
    return <p className="text-sm text-gray-500">No active session.</p>;
  }

  return (
    <div className="flex h-80 flex-col overflow-y-auto rounded-xl border border-gray-800 bg-gray-900 p-3 text-sm">
      {events.length === 0 && (
        <p className="m-auto text-gray-600">Waiting for events…</p>
      )}
      {events.map((ev) => (
        <div key={ev.id} className="mb-0.5 flex gap-2">
          <span className="shrink-0 text-base leading-5">
            {TYPE_ICON[ev.event_type] ?? '📌'}
          </span>
          <span className={TYPE_STYLE[ev.event_type] ?? 'text-gray-400'}>
            {ev.user_name && (
              <span className="font-medium text-gray-200">{ev.user_name}: </span>
            )}
            {ev.message || formatAmount(ev)}
          </span>
          <span className="ml-auto shrink-0 text-[10px] text-gray-600">
            {new Date(ev.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
          </span>
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}

function formatAmount(ev: StreamEvent): string {
  if (ev.amount && ev.event_type === 'donation') return `$${(ev.amount / 100).toFixed(2)}`;
  if (ev.amount && ev.event_type === 'bits')     return `${ev.amount} bits`;
  if (ev.amount && ev.event_type === 'gifted_sub') return `gifted ${ev.amount} subs`;
  return ev.event_type;
}
