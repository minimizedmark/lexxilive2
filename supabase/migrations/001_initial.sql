-- =============================================================================
-- 001_initial.sql — Initial schema for the AI Live Stream project
-- =============================================================================
-- Apply with:  supabase db push  (or paste into Supabase SQL editor)
-- =============================================================================

-- ---------------------------------------------------------------------------
-- creators
-- ---------------------------------------------------------------------------
create table if not exists creators (
  id                       uuid        primary key default gen_random_uuid(),
  slug                     text        unique not null,
  name                     text        not null,
  pitch_shift              int         not null default 0,
  description              text        not null default '',
  tags                     text[]      not null default '{}',
  -- Full persona block consumed by Brain / CreatorPersona (system_prompt,
  -- speaking_style, topics, catchphrases, chat_response_rate, etc.)
  persona                  jsonb       not null default '{}',
  twitch_channel           text        not null default '',
  youtube_channel_id       text        not null default '',
  elevenlabs_voice_id      text        not null default '',
  -- Supabase Storage paths (bucket-relative, e.g. "lexi/avatar.png")
  avatar_storage_path      text        not null default '',
  voice_model_storage_path text        not null default '',
  voice_index_storage_path text        not null default '',
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- stream_sessions
-- ---------------------------------------------------------------------------
create table if not exists stream_sessions (
  id             uuid        primary key default gen_random_uuid(),
  creator_id     uuid        references creators(id) on delete set null,
  started_at     timestamptz not null default now(),
  ended_at       timestamptz,
  platform       text        not null default 'manual',
  peak_viewers   int         not null default 0,
  total_events   int         not null default 0,
  current_state  text        not null default 'idle',
  -- Stores { valence: float, arousal: float, label: string } from the AI
  current_emotion jsonb      not null default '{}',
  -- 'live' | 'ended' | 'error'
  status         text        not null default 'live'
);

-- ---------------------------------------------------------------------------
-- stream_events
-- ---------------------------------------------------------------------------
create table if not exists stream_events (
  id          uuid        primary key default gen_random_uuid(),
  session_id  uuid        references stream_sessions(id) on delete cascade,
  -- e.g. 'chat_message', 'subscription', 'raid', 'donation', 'bits', 'follow'
  event_type  text        not null,
  user_name   text        not null default '',
  message     text        not null default '',
  -- Donation amount (cents) / bits count / gifted sub count
  amount      int         not null default 0,
  metadata    jsonb       not null default '{}',
  created_at  timestamptz not null default now()
);

-- Index for fast per-session queries (most common access pattern)
create index if not exists stream_events_session_id_idx
  on stream_events (session_id, created_at desc);

-- ---------------------------------------------------------------------------
-- viewers  (cross-session viewer memory)
-- ---------------------------------------------------------------------------
create table if not exists viewers (
  id                  uuid        primary key default gen_random_uuid(),
  platform            text        not null,
  platform_user_id    text        not null,
  display_name        text        not null,
  first_seen_at       timestamptz not null default now(),
  last_seen_at        timestamptz not null default now(),
  total_interactions  int         not null default 0,
  -- Free-form notes the AI can attach after notable interactions
  session_notes       text        not null default '',
  unique (platform, platform_user_id)
);

-- ---------------------------------------------------------------------------
-- stream_commands  (dashboard → Python bridge via Realtime)
-- ---------------------------------------------------------------------------
create table if not exists stream_commands (
  id          uuid        primary key default gen_random_uuid(),
  -- 'switch_creator' | 'inject_event' | 'set_mode'
  action      text        not null,
  -- Action-specific data, e.g. { "slug": "lexi" } or { "mode": "hype" }
  payload     jsonb       not null default '{}',
  -- Optional: scope the command to a specific live session
  session_id  uuid        references stream_sessions(id) on delete set null,
  -- Set by the bridge when the command is forwarded to Python
  executed_at timestamptz,
  created_at  timestamptz not null default now()
);

-- Fast lookup of pending (unexecuted) commands for a session
create index if not exists stream_commands_pending_idx
  on stream_commands (session_id, created_at desc)
  where executed_at is null;

-- =============================================================================
-- Row Level Security
-- =============================================================================
-- All tables enable RLS. The Node.js server uses the service-role key which
-- bypasses RLS entirely. These policies exist for any future authenticated
-- dashboard users.
-- =============================================================================

alter table creators        enable row level security;
alter table stream_sessions enable row level security;
alter table stream_events   enable row level security;
alter table viewers         enable row level security;
alter table stream_commands enable row level security;

-- Service-role key bypasses RLS automatically (Supabase behaviour).
--
-- Phase 1: allow any authenticated user to read public data, and only the
-- service role (backend) to write.  Expand these as you add user accounts.

-- creators — public read (dashboard/viewers can browse), service-only write
create policy "Anyone authenticated can read creators"
  on creators for select
  using (auth.role() = 'authenticated');

-- stream_sessions — authenticated read, service-only write
create policy "Anyone authenticated can read sessions"
  on stream_sessions for select
  using (auth.role() = 'authenticated');

-- stream_events — authenticated read, service-only write
create policy "Anyone authenticated can read events"
  on stream_events for select
  using (auth.role() = 'authenticated');

-- viewers — service-only (internal use, not exposed to dashboard users yet)
-- No policy added; RLS blocks all non-service access by default.

-- stream_commands — authenticated insert (dashboard can send commands),
-- service-only update/delete
create policy "Authenticated users can insert commands"
  on stream_commands for insert
  with check (auth.role() = 'authenticated');

create policy "Authenticated users can read their commands"
  on stream_commands for select
  using (auth.role() = 'authenticated');

-- =============================================================================
-- updated_at trigger for creators
-- =============================================================================

create or replace function set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists creators_set_updated_at on creators;
create trigger creators_set_updated_at
  before update on creators
  for each row execute procedure set_updated_at();

-- =============================================================================
-- Helper RPC: increment_session_events
-- Called by the events route and stream bridge after each event insert.
-- =============================================================================

create or replace function increment_session_events(p_session_id uuid)
returns void language plpgsql security definer as $$
begin
  update stream_sessions
     set total_events = total_events + 1
   where id = p_session_id;
end;
$$;

-- =============================================================================
-- Realtime — enable Realtime for stream_commands so the bridge can subscribe
-- =============================================================================
-- Run these in the Supabase dashboard → Database → Replication, OR they are
-- handled automatically if you use supabase CLI with realtime enabled in
-- config.toml.  Included here for documentation purposes.
--
-- alter publication supabase_realtime add table stream_commands;
