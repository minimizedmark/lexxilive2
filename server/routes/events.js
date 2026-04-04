import { Router } from 'express';
import supabase from '../supabase.js';

const router = Router();

// POST /api/events — log a stream event (called by Python bridge)
router.post('/', async (req, res) => {
  try {
    const {
      session_id,
      event_type,
      user_name = '',
      message = '',
      amount = 0,
      metadata = {},
    } = req.body;

    if (!session_id) return res.status(400).json({ error: 'session_id is required' });
    if (!event_type) return res.status(400).json({ error: 'event_type is required' });

    const { data, error } = await supabase
      .from('stream_events')
      .insert({
        session_id,
        event_type,
        user_name,
        message,
        amount,
        metadata,
      })
      .select()
      .single();

    if (error) throw error;

    // Increment the session's total_events counter atomically via RPC.
    // Swallow errors here — the event is already saved.
    await supabase.rpc('increment_session_events', { p_session_id: session_id }).catch(() => {});

    res.status(201).json(data);
  } catch (err) {
    console.error('[events] create error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// GET /api/events?session_id=<uuid>&limit=<n>&event_type=<type>
router.get('/', async (req, res) => {
  try {
    const { session_id, event_type, limit: rawLimit } = req.query;
    const limit = Math.min(parseInt(rawLimit) || 100, 500);

    if (!session_id) return res.status(400).json({ error: 'session_id query param is required' });

    let query = supabase
      .from('stream_events')
      .select('*')
      .eq('session_id', session_id)
      .order('created_at', { ascending: false })
      .limit(limit);

    if (event_type) query = query.eq('event_type', event_type);

    const { data, error } = await query;
    if (error) throw error;
    res.json(data);
  } catch (err) {
    console.error('[events] list error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

export default router;
