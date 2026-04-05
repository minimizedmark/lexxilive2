import { Router } from 'express';
import multer from 'multer';
import supabase from '../supabase.js';
import { syncCreator } from '../services/sync.js';

const router = Router({ mergeParams: true });

// Store uploads in memory so we can stream them straight to Supabase Storage.
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 500 * 1024 * 1024 }, // 500 MB ceiling for voice models
});

// ---------------------------------------------------------------------------
// Helper — upload a buffer to a Supabase Storage bucket and return public URL.
// ---------------------------------------------------------------------------
async function uploadToStorage(bucket, storagePath, buffer, mimeType) {
  const { error } = await supabase.storage
    .from(bucket)
    .upload(storagePath, buffer, {
      contentType: mimeType,
      upsert: true,
    });
  if (error) throw error;

  const { data } = supabase.storage.from(bucket).getPublicUrl(storagePath);
  return data.publicUrl;
}

// ---------------------------------------------------------------------------
// POST /api/creators/:slug/avatar
// ---------------------------------------------------------------------------
router.post('/:slug/avatar', upload.single('file'), async (req, res) => {
  try {
    if (!req.file) return res.status(400).json({ error: 'No file uploaded' });

    const { slug } = req.params;
    const storagePath = `${slug}/avatar.png`;

    const publicUrl = await uploadToStorage(
      'avatars',
      storagePath,
      req.file.buffer,
      'image/png'
    );

    // Persist the storage path back to the creators row.
    const { error: dbError } = await supabase
      .from('creators')
      .update({
        avatar_storage_path: storagePath,
        updated_at: new Date().toISOString(),
      })
      .eq('slug', slug);

    if (dbError) throw dbError;

    res.json({ storagePath, publicUrl });
  } catch (err) {
    console.error('[assets] avatar upload error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// ---------------------------------------------------------------------------
// POST /api/creators/:slug/voice-model
// ---------------------------------------------------------------------------
router.post('/:slug/voice-model', upload.single('file'), async (req, res) => {
  try {
    if (!req.file) return res.status(400).json({ error: 'No file uploaded' });

    const { slug } = req.params;
    const storagePath = `${slug}/voice.pth`;

    const publicUrl = await uploadToStorage(
      'voice-models',
      storagePath,
      req.file.buffer,
      'application/octet-stream'
    );

    const { error: dbError } = await supabase
      .from('creators')
      .update({
        voice_model_storage_path: storagePath,
        updated_at: new Date().toISOString(),
      })
      .eq('slug', slug);

    if (dbError) throw dbError;

    res.json({ storagePath, publicUrl });
  } catch (err) {
    console.error('[assets] voice-model upload error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// ---------------------------------------------------------------------------
// POST /api/creators/:slug/voice-index
// ---------------------------------------------------------------------------
router.post('/:slug/voice-index', upload.single('file'), async (req, res) => {
  try {
    if (!req.file) return res.status(400).json({ error: 'No file uploaded' });

    const { slug } = req.params;
    const storagePath = `${slug}/voice.index`;

    const publicUrl = await uploadToStorage(
      'voice-models',
      storagePath,
      req.file.buffer,
      'application/octet-stream'
    );

    const { error: dbError } = await supabase
      .from('creators')
      .update({
        voice_index_storage_path: storagePath,
        updated_at: new Date().toISOString(),
      })
      .eq('slug', slug);

    if (dbError) throw dbError;

    res.json({ storagePath, publicUrl });
  } catch (err) {
    console.error('[assets] voice-index upload error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// ---------------------------------------------------------------------------
// GET /api/creators/:slug/download
// Trigger an on-demand sync of this creator's assets to the local creators/ dir.
// ---------------------------------------------------------------------------
router.get('/:slug/download', async (req, res) => {
  try {
    const { slug } = req.params;
    const result = await syncCreator(slug);
    res.json({ ok: true, slug, ...result });
  } catch (err) {
    console.error('[assets] download error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

export default router;
