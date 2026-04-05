import { createClient } from '@supabase/supabase-js';

const url = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;

export const supabase = createClient(url, key);

// ---------------------------------------------------------------------------
// Shared types matching the DB schema
// ---------------------------------------------------------------------------

export type Creator = {
  id: string;
  slug: string;
  name: string;
  pitch_shift: number;
  description: string;
  tags: string[];
  persona: Record<string, unknown>;
  twitch_channel: string;
  youtube_channel_id: string;
  elevenlabs_voice_id: string;
  avatar_storage_path: string;
  voice_model_storage_path: string;
  voice_index_storage_path: string;
  created_at: string;
  updated_at: string;
};

export type StreamSession = {
  id: string;
  creator_id: string | null;
  started_at: string;
  ended_at: string | null;
  platform: string;
  peak_viewers: number;
  total_events: number;
  current_state: string;
  current_emotion: { valence?: number; arousal?: number; label?: string };
  status: 'live' | 'ended' | 'error';
};

export type StreamEvent = {
  id: string;
  session_id: string;
  event_type: string;
  user_name: string;
  message: string;
  amount: number;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type StreamCommand = {
  action: 'switch_creator' | 'inject_event' | 'set_mode';
  payload: Record<string, unknown>;
  session_id?: string;
};
