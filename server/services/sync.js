/**
 * sync.js — Download creator assets from Supabase Storage to the local
 * creators/<slug>/ directory that the Python stream engine reads at startup.
 *
 * For each creator row the service:
 *   1. Creates creators/<slug>/  if it doesn't exist.
 *   2. Writes config.json in the exact format Python's creator.py expects.
 *   3. Downloads avatar.png, voice.pth, and voice.index from Storage (only if
 *      the storage paths are set on the DB row).
 */

import { writeFile, mkdir } from 'fs/promises';
import { existsSync } from 'fs';
import path from 'path';
import supabase from '../supabase.js';

const CREATORS_DIR = process.env.CREATORS_DIR
  ? path.resolve(process.env.CREATORS_DIR)
  : path.resolve('..', 'creators');

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Sync all creators from the database to the local filesystem.
 * Called once at server startup.
 */
export async function syncAllCreators() {
  const { data: creators, error } = await supabase.from('creators').select('*');
  if (error) {
    console.error('[sync] Failed to fetch creators:', error.message);
    return;
  }

  console.log(`[sync] Syncing ${creators.length} creator(s) to ${CREATORS_DIR}`);
  await Promise.all(creators.map((c) => syncCreatorRow(c)));
  console.log('[sync] Done.');
}

/**
 * Sync a single creator by slug (on-demand, e.g. after an asset upload).
 * Returns a summary of what was written.
 */
export async function syncCreator(slug) {
  const { data, error } = await supabase
    .from('creators')
    .select('*')
    .eq('slug', slug)
    .single();

  if (error) throw new Error(`Creator '${slug}' not found: ${error.message}`);
  return syncCreatorRow(data);
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

async function syncCreatorRow(creator) {
  const dir = path.join(CREATORS_DIR, creator.slug);
  await mkdir(dir, { recursive: true });

  const written = [];

  // --- config.json (Python-compatible schema) ---
  const config = buildConfig(creator);
  await writeFile(path.join(dir, 'config.json'), JSON.stringify(config, null, 2), 'utf-8');
  written.push('config.json');

  // --- avatar.png ---
  if (creator.avatar_storage_path) {
    try {
      await downloadAsset('avatars', creator.avatar_storage_path, path.join(dir, 'avatar.png'));
      written.push('avatar.png');
    } catch (err) {
      console.warn(`[sync] ${creator.slug} avatar download failed: ${err.message}`);
    }
  }

  // --- voice.pth ---
  if (creator.voice_model_storage_path) {
    try {
      await downloadAsset('voice-models', creator.voice_model_storage_path, path.join(dir, 'voice.pth'));
      written.push('voice.pth');
    } catch (err) {
      console.warn(`[sync] ${creator.slug} voice model download failed: ${err.message}`);
    }
  }

  // --- voice.index ---
  if (creator.voice_index_storage_path) {
    try {
      await downloadAsset('voice-models', creator.voice_index_storage_path, path.join(dir, 'voice.index'));
      written.push('voice.index');
    } catch (err) {
      console.warn(`[sync] ${creator.slug} voice index download failed: ${err.message}`);
    }
  }

  console.log(`[sync] ${creator.slug}: wrote ${written.join(', ')}`);
  return { written };
}

/**
 * Build a config.json object that matches the Python Creator config schema
 * documented in src/creator.py.
 *
 * The 'persona' JSONB column stores the full persona block used by Brain /
 * CreatorPersona (system_prompt, speaking_style, topics, catchphrases, etc.).
 */
function buildConfig(creator) {
  const config = {
    // Core fields read by load_creator()
    name: creator.name,
    pitch_shift: creator.pitch_shift ?? 0,
    description: creator.description ?? '',
    tags: creator.tags ?? [],

    // Explicit file names — Python will look for these first.
    // These match the filenames we use when downloading from Storage.
    avatar_file: 'avatar.png',
    voice_model_file: 'voice.pth',
    voice_index_file: 'voice.index',

    // Platform integrations (used by AutomationEngine / chat readers)
    twitch_channel: creator.twitch_channel ?? '',
    youtube_channel_id: creator.youtube_channel_id ?? '',

    // ElevenLabs TTS voice (used by TTSEngine when available)
    elevenlabs_voice_id: creator.elevenlabs_voice_id ?? '',
  };

  // Embed the full persona block only when it has content.
  // Python's Brain reads creator_cfg.get('persona', {}) directly.
  if (creator.persona && typeof creator.persona === 'object' && Object.keys(creator.persona).length > 0) {
    config.persona = creator.persona;
  }

  return config;
}

/**
 * Download a single file from Supabase Storage and save it to localPath.
 * Uses the admin download API (bypasses RLS).
 */
async function downloadAsset(bucket, storagePath, localPath) {
  const { data, error } = await supabase.storage.from(bucket).download(storagePath);
  if (error) throw new Error(error.message);

  const arrayBuffer = await data.arrayBuffer();
  await writeFile(localPath, Buffer.from(arrayBuffer));
}
