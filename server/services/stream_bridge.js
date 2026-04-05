/**
 * stream_bridge.js — WebSocket server that bridges the Node.js backend
 * and the Python stream engine.
 *
 * Protocol (all messages are JSON):
 *
 *   Python → Node:
 *     { type: 'event',  payload: { session_id, event_type, user_name, message, amount, metadata } }
 *     { type: 'state',  payload: { state, emotion, creator } }
 *     { type: 'hello',  payload: { session_id } }        ← sent on connect
 *
 *   Node → Python:
 *     { type: 'command', action: 'switch_creator', slug: '...' }
 *     { type: 'command', action: 'inject_event',   event: {...} }
 *     { type: 'command', action: 'set_mode',        mode: '...' }
 *
 * Supabase Realtime:
 *   The bridge subscribes to INSERT events on stream_commands and forwards
 *   each new command row to all connected Python clients.
 */

import { WebSocketServer, WebSocket } from 'ws';
import supabase from '../supabase.js';

// Tracks all currently connected Python WebSocket clients.
// Key: ws instance  Value: { session_id: string | null }
const clients = new Map();

// The single Supabase Realtime channel for stream_commands.
let realtimeChannel = null;

// ---------------------------------------------------------------------------
// Public API — called from index.js
// ---------------------------------------------------------------------------

/**
 * Attach the WebSocket server to an existing http.Server instance.
 * This lets HTTP and WS share the same port.
 */
export function attachWebSocketServer(httpServer) {
  const wss = new WebSocketServer({ server: httpServer, path: '/ws' });

  wss.on('connection', (ws, req) => {
    console.log(`[bridge] Python client connected from ${req.socket.remoteAddress}`);
    clients.set(ws, { session_id: null });

    ws.on('message', (raw) => handleMessage(ws, raw));
    ws.on('close',   ()  => {
      clients.delete(ws);
      console.log('[bridge] Python client disconnected');
    });
    ws.on('error', (err) => {
      console.error('[bridge] WebSocket error:', err.message);
    });
  });

  console.log('[bridge] WebSocket server attached at path /ws');

  // Start listening for dashboard commands from Supabase Realtime.
  subscribeToCommands();
}

// ---------------------------------------------------------------------------
// Inbound message handling (Python → Node)
// ---------------------------------------------------------------------------

async function handleMessage(ws, raw) {
  let msg;
  try {
    msg = JSON.parse(raw.toString());
  } catch {
    console.warn('[bridge] Received non-JSON message, ignoring.');
    return;
  }

  switch (msg.type) {
    case 'hello':
      // Python identifies which session it is running for.
      if (msg.payload?.session_id) {
        const meta = clients.get(ws);
        if (meta) meta.session_id = msg.payload.session_id;
        console.log(`[bridge] Session registered: ${msg.payload.session_id}`);
      }
      break;

    case 'event':
      await handleStreamEvent(ws, msg.payload);
      break;

    case 'state':
      await handleStateUpdate(ws, msg.payload);
      break;

    default:
      console.warn(`[bridge] Unknown message type: ${msg.type}`);
  }
}

/**
 * Log a stream event from Python to the Supabase stream_events table.
 * The Python process passes the same shape as POST /api/events.
 */
async function handleStreamEvent(ws, payload) {
  if (!payload) return;

  const {
    session_id,
    event_type,
    user_name = '',
    message   = '',
    amount    = 0,
    metadata  = {},
  } = payload;

  // Fall back to the session_id registered at hello-time.
  const meta = clients.get(ws);
  const sid  = session_id || meta?.session_id;

  if (!sid || !event_type) {
    console.warn('[bridge] event missing session_id or event_type, skipping.');
    return;
  }

  try {
    const { error } = await supabase.from('stream_events').insert({
      session_id: sid,
      event_type,
      user_name,
      message,
      amount,
      metadata,
    });
    if (error) throw error;

    // Increment the session event counter (best-effort).
    await supabase
      .rpc('increment_session_events', { p_session_id: sid })
      .catch(() => {});
  } catch (err) {
    console.error('[bridge] Failed to save stream event:', err.message);
  }
}

/**
 * Persist the current AI state (emotion, creator, stream state label) back
 * to the live session row.
 */
async function handleStateUpdate(ws, payload) {
  if (!payload) return;

  const { state, emotion, creator } = payload;
  const meta = clients.get(ws);
  const sid  = meta?.session_id;

  if (!sid) return;

  const updates = {};
  if (state)   updates.current_state   = state;
  if (emotion) updates.current_emotion = emotion;

  // If the creator changed, look up and store the creator_id.
  if (creator) {
    try {
      const { data } = await supabase
        .from('creators')
        .select('id')
        .eq('slug', creator)
        .single();
      if (data) updates.creator_id = data.id;
    } catch {
      // Non-fatal: creator might not be in DB yet.
    }
  }

  if (Object.keys(updates).length === 0) return;

  try {
    const { error } = await supabase
      .from('stream_sessions')
      .update(updates)
      .eq('id', sid);
    if (error) throw error;
  } catch (err) {
    console.error('[bridge] Failed to update session state:', err.message);
  }
}

// ---------------------------------------------------------------------------
// Outbound commands (Node → Python)
// ---------------------------------------------------------------------------

/**
 * Broadcast a command object to all connected Python clients.
 * If session_id is provided, only send to the matching client.
 */
export function sendCommand(command, sessionId = null) {
  const payload = JSON.stringify(command);
  let sent = 0;

  for (const [ws, meta] of clients) {
    if (ws.readyState !== WebSocket.OPEN) continue;
    if (sessionId && meta.session_id !== sessionId) continue;
    ws.send(payload);
    sent++;
  }

  if (sent === 0) {
    console.warn('[bridge] sendCommand: no matching connected clients');
  }
  return sent;
}

// ---------------------------------------------------------------------------
// Supabase Realtime — stream_commands table
// ---------------------------------------------------------------------------

/**
 * Subscribe to INSERT events on stream_commands.
 * Each new row becomes a command pushed to the Python process.
 *
 * The row shape expected in the 'payload' column:
 *   { action: 'switch_creator' | 'inject_event' | 'set_mode', ...actionSpecificFields }
 */
function subscribeToCommands() {
  if (realtimeChannel) {
    supabase.removeChannel(realtimeChannel);
  }

  realtimeChannel = supabase
    .channel('stream_commands_inserts')
    .on(
      'postgres_changes',
      { event: 'INSERT', schema: 'public', table: 'stream_commands' },
      async (change) => {
        const row = change.new;
        if (!row) return;

        const command = {
          type:    'command',
          action:  row.action,
          // Spread the payload object (e.g. { slug } for switch_creator)
          ...((row.payload && typeof row.payload === 'object') ? row.payload : {}),
          // Include the DB row id so Python can ack if needed
          command_id: row.id,
        };

        const sent = sendCommand(command, row.session_id || null);
        console.log(`[bridge] Realtime command '${row.action}' sent to ${sent} client(s)`);

        // Mark the command as executed.
        await supabase
          .from('stream_commands')
          .update({ executed_at: new Date().toISOString() })
          .eq('id', row.id)
          .catch((err) => console.warn('[bridge] Failed to mark command executed:', err.message));
      }
    )
    .subscribe((status) => {
      console.log(`[bridge] Realtime subscription status: ${status}`);
    });
}
