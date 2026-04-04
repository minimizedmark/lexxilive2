import { Router } from 'express';
import supabase from '../supabase.js';

const router = Router();

// POST /api/sessions — start a new stream session
router.post('/', async (req, res) => {
  try {
    const {
      creator_id,
      platform = 'manual',
    } = req.body;

    // Resolve creator_id from slug if a string slug was passed instead.
    let resolvedCreatorId = creator_id;
    if (creator_id && !isUuid(creator_id)) {
      const { data: creator, error: cErr } = await supabase
        .from('creators')
        .select('id')
        .eq('slug', creator_id)
        .single();
      if (cErr || !creator) return res.status(404).json({ error: 'Creator not found' });
      resolvedCreatorId = creator.id;
    }

    const { data, error } = await supabase
      .from('stream_sessions')
      .insert({
        creator_id: resolvedCreatorId || null,
        platform,
        status: 'live',
        started_at: new Date().toISOString(),
      })
      .select()
      .single();

    if (error) throw error;
    res.status(201).json(data);
  } catch (err) {
    console.error('[sessions] create error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// PATCH /api/sessions/:id — update session (e.g. end it, update stats)
router.patch('/:id', async (req, res) => {
  try {
    const allowed = [
      'ended_at', 'peak_viewers', 'total_events',
      'current_state', 'current_emotion', 'status',
    ];

    const updates = {};
    for (const key of allowed) {
      if (key in req.body) updates[key] = req.body[key];
    }

    // Convenience: passing status:'ended' auto-sets ended_at if not already set.
    if (updates.status === 'ended' && !updates.ended_at) {
      updates.ended_at = new Date().toISOString();
    }

    if (Object.keys(updates).length === 0) {
      return res.status(400).json({ error: 'No valid fields to update' });
    }

    const { data, error } = await supabase
      .from('stream_sessions')
      .update(updates)
      .eq('id', req.params.id)
      .select()
      .single();

    if (error) {
      if (error.code === 'PGRST116') return res.status(404).json({ error: 'Session not found' });
      throw error;
    }
    res.json(data);
  } catch (err) {
    console.error('[sessions] update error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// GET /api/sessions — list recent sessions (most recent first)
router.get('/', async (req, res) => {
  try {
    const limit = Math.min(parseInt(req.query.limit) || 20, 100);
    const status = req.query.status; // optional filter: 'live' | 'ended'

    let query = supabase
      .from('stream_sessions')
      .select(`
        *,
        creators ( slug, name )
      `)
      .order('started_at', { ascending: false })
      .limit(limit);

    if (status) query = query.eq('status', status);

    const { data, error } = await query;
    if (error) throw error;
    res.json(data);
  } catch (err) {
    console.error('[sessions] list error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function isUuid(str) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(str);
}

export default router;
