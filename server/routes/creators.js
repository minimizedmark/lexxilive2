import { Router } from 'express';
import supabase from '../supabase.js';

const router = Router();

// GET /api/creators — list all creator profiles
router.get('/', async (req, res) => {
  try {
    const { data, error } = await supabase
      .from('creators')
      .select('*')
      .order('slug');

    if (error) throw error;
    res.json(data);
  } catch (err) {
    console.error('[creators] list error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// GET /api/creators/:slug — get one creator by slug
router.get('/:slug', async (req, res) => {
  try {
    const { data, error } = await supabase
      .from('creators')
      .select('*')
      .eq('slug', req.params.slug)
      .single();

    if (error) {
      if (error.code === 'PGRST116') return res.status(404).json({ error: 'Creator not found' });
      throw error;
    }
    res.json(data);
  } catch (err) {
    console.error('[creators] get error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// POST /api/creators — create or upsert a creator profile
router.post('/', async (req, res) => {
  try {
    const {
      slug,
      name,
      pitch_shift = 0,
      description = '',
      tags = [],
      persona = {},
      twitch_channel = '',
      youtube_channel_id = '',
      elevenlabs_voice_id = '',
    } = req.body;

    if (!slug || !name) {
      return res.status(400).json({ error: 'slug and name are required' });
    }

    const { data, error } = await supabase
      .from('creators')
      .upsert(
        {
          slug,
          name,
          pitch_shift,
          description,
          tags,
          persona,
          twitch_channel,
          youtube_channel_id,
          elevenlabs_voice_id,
          updated_at: new Date().toISOString(),
        },
        { onConflict: 'slug', returning: 'representation' }
      )
      .select()
      .single();

    if (error) throw error;
    res.status(201).json(data);
  } catch (err) {
    console.error('[creators] create error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// PATCH /api/creators/:slug — partial update
router.patch('/:slug', async (req, res) => {
  try {
    const allowed = [
      'name', 'pitch_shift', 'description', 'tags', 'persona',
      'twitch_channel', 'youtube_channel_id', 'elevenlabs_voice_id',
      'avatar_storage_path', 'voice_model_storage_path', 'voice_index_storage_path',
    ];

    const updates = {};
    for (const key of allowed) {
      if (key in req.body) updates[key] = req.body[key];
    }

    if (Object.keys(updates).length === 0) {
      return res.status(400).json({ error: 'No valid fields to update' });
    }

    updates.updated_at = new Date().toISOString();

    const { data, error } = await supabase
      .from('creators')
      .update(updates)
      .eq('slug', req.params.slug)
      .select()
      .single();

    if (error) {
      if (error.code === 'PGRST116') return res.status(404).json({ error: 'Creator not found' });
      throw error;
    }
    res.json(data);
  } catch (err) {
    console.error('[creators] update error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// DELETE /api/creators/:slug — delete a creator
router.delete('/:slug', async (req, res) => {
  try {
    const { error } = await supabase
      .from('creators')
      .delete()
      .eq('slug', req.params.slug);

    if (error) throw error;
    res.status(204).send();
  } catch (err) {
    console.error('[creators] delete error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

export default router;
