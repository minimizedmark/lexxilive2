const BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:3000';

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text}`);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Creators
// ---------------------------------------------------------------------------

export const getCreators  = ()            => req<unknown[]>('/api/creators');
export const getCreator   = (slug: string) => req<unknown>(`/api/creators/${slug}`);
export const createCreator = (body: unknown) =>
  req<unknown>('/api/creators', { method: 'POST', body: JSON.stringify(body) });
export const updateCreator = (slug: string, body: unknown) =>
  req<unknown>(`/api/creators/${slug}`, { method: 'PUT', body: JSON.stringify(body) });
export const deleteCreator = (slug: string) =>
  req<unknown>(`/api/creators/${slug}`, { method: 'DELETE' });

// ---------------------------------------------------------------------------
// Asset uploads (multipart/form-data)
// ---------------------------------------------------------------------------

export async function uploadAsset(
  slug: string,
  type: 'avatar' | 'voice-model' | 'voice-index',
  file: File,
) {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${BASE}/api/creators/${slug}/${type}`, {
    method: 'POST',
    body: form,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

export const getSessions  = () => req<unknown[]>('/api/sessions');
export const startSession = (body: unknown) =>
  req<unknown>('/api/sessions', { method: 'POST', body: JSON.stringify(body) });
export const endSession   = (id: string) =>
  req<unknown>(`/api/sessions/${id}/end`, { method: 'POST' });

// ---------------------------------------------------------------------------
// Commands (dashboard → Python via Node WS bridge)
// ---------------------------------------------------------------------------

export const sendCommand = (body: unknown) =>
  req<unknown>('/api/sessions/command', { method: 'POST', body: JSON.stringify(body) });
