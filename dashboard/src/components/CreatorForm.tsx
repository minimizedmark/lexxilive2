'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { getCreator, createCreator, updateCreator, uploadAsset } from '@/lib/api';
import type { Creator } from '@/lib/supabase';

type Props = { slug?: string };

const EMPTY: Partial<Creator> = {
  slug: '', name: '', description: '', pitch_shift: 0,
  tags: [], twitch_channel: '', youtube_channel_id: '',
  elevenlabs_voice_id: '',
  persona: {},
};

export default function CreatorForm({ slug }: Props) {
  const router  = useRouter();
  const isEdit  = Boolean(slug);

  const [form,    setForm]    = useState<Partial<Creator>>(EMPTY);
  const [persona, setPersona] = useState('{}');
  const [loading, setLoading] = useState(isEdit);
  const [saving,  setSaving]  = useState(false);
  const [error,   setError]   = useState('');
  const [success, setSuccess] = useState('');

  // Asset upload state
  const [avatarFile,     setAvatarFile]     = useState<File | null>(null);
  const [voiceModelFile, setVoiceModelFile] = useState<File | null>(null);
  const [voiceIndexFile, setVoiceIndexFile] = useState<File | null>(null);
  const [uploading,      setUploading]      = useState(false);

  useEffect(() => {
    if (!slug) return;
    getCreator(slug)
      .then((data) => {
        const c = data as Creator;
        setForm(c);
        setPersona(JSON.stringify(c.persona ?? {}, null, 2));
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, [slug]);

  function set(key: keyof Creator, value: unknown) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError('');
    setSuccess('');

    try {
      let parsedPersona: Record<string, unknown> = {};
      try { parsedPersona = JSON.parse(persona); } catch {
        throw new Error('Persona JSON is invalid');
      }

      const payload = {
        ...form,
        tags: Array.isArray(form.tags) ? form.tags : (form.tags as unknown as string)
          .split(',').map((t: string) => t.trim()).filter(Boolean),
        persona: parsedPersona,
      };

      let savedSlug = slug;
      if (isEdit) {
        await updateCreator(slug!, payload);
      } else {
        const created = await createCreator(payload) as Creator;
        savedSlug = created.slug;
      }

      // Upload any selected assets
      if (savedSlug && (avatarFile || voiceModelFile || voiceIndexFile)) {
        setUploading(true);
        await Promise.all([
          avatarFile     && uploadAsset(savedSlug, 'avatar',      avatarFile),
          voiceModelFile && uploadAsset(savedSlug, 'voice-model', voiceModelFile),
          voiceIndexFile && uploadAsset(savedSlug, 'voice-index', voiceIndexFile),
        ].filter(Boolean));
        setUploading(false);
      }

      setSuccess(isEdit ? 'Saved!' : 'Creator created!');
      if (!isEdit) router.push(`/creators/${savedSlug}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
      setUploading(false);
    }
  }

  if (loading) return <p className="text-gray-500">Loading…</p>;

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5">
      {error   && <Alert type="error">{error}</Alert>}
      {success && <Alert type="success">{success}</Alert>}

      <Section title="Identity">
        <Field label="Slug" hint="URL-safe, e.g. lexi">
          <input value={form.slug ?? ''} onChange={(e) => set('slug', e.target.value)}
            required disabled={isEdit}
            placeholder="lexi"
            className={inputCls + (isEdit ? ' opacity-50 cursor-not-allowed' : '')} />
        </Field>
        <Field label="Display Name">
          <input value={form.name ?? ''} onChange={(e) => set('name', e.target.value)}
            required placeholder="Lexi" className={inputCls} />
        </Field>
        <Field label="Description">
          <textarea value={form.description ?? ''} rows={2}
            onChange={(e) => set('description', e.target.value)}
            placeholder="Energetic gaming streamer…" className={inputCls} />
        </Field>
        <Field label="Tags" hint="Comma-separated">
          <input
            value={Array.isArray(form.tags) ? form.tags.join(', ') : (form.tags ?? '')}
            onChange={(e) => set('tags', e.target.value)}
            placeholder="gaming, anime" className={inputCls} />
        </Field>
      </Section>

      <Section title="Voice">
        <Field label="Pitch Shift (semitones)" hint="-12 to +12">
          <input type="number" min={-24} max={24}
            value={form.pitch_shift ?? 0}
            onChange={(e) => set('pitch_shift', parseInt(e.target.value))}
            className={inputCls + ' w-28'} />
        </Field>
        <Field label="ElevenLabs Voice ID" hint="Optional">
          <input value={form.elevenlabs_voice_id ?? ''}
            onChange={(e) => set('elevenlabs_voice_id', e.target.value)}
            placeholder="EXAVITQu4vr4xnSDxMaL" className={inputCls} />
        </Field>
      </Section>

      <Section title="Platform">
        <Field label="Twitch Channel" hint="Username only, no #">
          <input value={form.twitch_channel ?? ''}
            onChange={(e) => set('twitch_channel', e.target.value)}
            placeholder="lexi" className={inputCls} />
        </Field>
        <Field label="YouTube Channel ID">
          <input value={form.youtube_channel_id ?? ''}
            onChange={(e) => set('youtube_channel_id', e.target.value)}
            placeholder="UCxxxxxxx" className={inputCls} />
        </Field>
      </Section>

      {isEdit && (
        <Section title="Assets">
          <Field label="Avatar PNG">
            <input type="file" accept="image/png,image/webp"
              onChange={(e) => setAvatarFile(e.target.files?.[0] ?? null)}
              className="text-sm text-gray-400 file:mr-3 file:rounded-lg file:border-0 file:bg-brand-600 file:px-3 file:py-1.5 file:text-sm file:text-white file:cursor-pointer" />
          </Field>
          <Field label="Voice Model (.pth)">
            <input type="file" accept=".pth,.pt"
              onChange={(e) => setVoiceModelFile(e.target.files?.[0] ?? null)}
              className="text-sm text-gray-400 file:mr-3 file:rounded-lg file:border-0 file:bg-brand-600 file:px-3 file:py-1.5 file:text-sm file:text-white file:cursor-pointer" />
          </Field>
          <Field label="Voice Index (.index)">
            <input type="file" accept=".index"
              onChange={(e) => setVoiceIndexFile(e.target.files?.[0] ?? null)}
              className="text-sm text-gray-400 file:mr-3 file:rounded-lg file:border-0 file:bg-brand-600 file:px-3 file:py-1.5 file:text-sm file:text-white file:cursor-pointer" />
          </Field>
        </Section>
      )}

      <Section title="Persona (JSON)" hint="system_prompt, speaking_style, catchphrases, topics, chat_response_rate…">
        <textarea
          value={persona}
          onChange={(e) => setPersona(e.target.value)}
          rows={10}
          spellCheck={false}
          className={inputCls + ' font-mono text-xs'}
        />
      </Section>

      <button
        type="submit"
        disabled={saving || uploading}
        className="rounded-lg bg-brand-600 py-2.5 font-semibold hover:bg-brand-700 disabled:opacity-50 transition-colors"
      >
        {uploading ? 'Uploading assets…' : saving ? 'Saving…' : isEdit ? 'Save Changes' : 'Create Creator'}
      </button>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Small helper components
// ---------------------------------------------------------------------------

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <fieldset className="rounded-xl border border-gray-800 bg-gray-900 p-5">
      <legend className="mb-3 px-1 text-sm font-semibold text-gray-300">
        {title}
        {hint && <span className="ml-2 font-normal text-gray-500">{hint}</span>}
      </legend>
      <div className="flex flex-col gap-3">{children}</div>
    </fieldset>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium text-gray-400">
        {label}
        {hint && <span className="ml-1 font-normal text-gray-600">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

function Alert({ type, children }: { type: 'error' | 'success'; children: React.ReactNode }) {
  const cls = type === 'error'
    ? 'border-red-700 bg-red-900/30 text-red-300'
    : 'border-green-700 bg-green-900/30 text-green-300';
  return (
    <div className={`rounded-lg border p-3 text-sm ${cls}`}>{children}</div>
  );
}

const inputCls =
  'w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 placeholder-gray-600 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500';
