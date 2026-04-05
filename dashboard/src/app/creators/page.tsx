'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { getCreators, deleteCreator } from '@/lib/api';
import type { Creator } from '@/lib/supabase';

export default function CreatorsPage() {
  const [creators, setCreators] = useState<Creator[]>([]);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState('');

  async function load() {
    try {
      setLoading(true);
      const data = await getCreators();
      setCreators(data as Creator[]);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load creators');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function handleDelete(slug: string) {
    if (!confirm(`Delete creator "${slug}"? This cannot be undone.`)) return;
    try {
      await deleteCreator(slug);
      setCreators((prev) => prev.filter((c) => c.slug !== slug));
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : 'Delete failed');
    }
  }

  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold">Creators</h1>
        <Link
          href="/creators/new"
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold hover:bg-brand-700 transition-colors"
        >
          + New Creator
        </Link>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-700 bg-red-900/30 p-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {loading ? (
        <p className="text-gray-500">Loading…</p>
      ) : creators.length === 0 ? (
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-12 text-center">
          <p className="text-gray-400">No creators yet.</p>
          <Link href="/creators/new" className="mt-3 inline-block text-brand-500 hover:underline">
            Add your first creator
          </Link>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {creators.map((c) => (
            <CreatorCard key={c.id} creator={c} onDelete={handleDelete} />
          ))}
        </div>
      )}
    </div>
  );
}

function CreatorCard({
  creator,
  onDelete,
}: {
  creator: Creator;
  onDelete: (slug: string) => void;
}) {
  return (
    <div className="flex flex-col rounded-xl border border-gray-800 bg-gray-900 overflow-hidden">
      {/* Avatar preview */}
      <div className="flex h-36 items-center justify-center bg-gray-800">
        {creator.avatar_storage_path ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={`${process.env.NEXT_PUBLIC_SUPABASE_URL}/storage/v1/object/public/avatars/${creator.avatar_storage_path}`}
            alt={creator.name}
            className="h-full w-full object-contain"
          />
        ) : (
          <span className="text-4xl">🎭</span>
        )}
      </div>

      <div className="flex flex-1 flex-col gap-2 p-4">
        <div>
          <h2 className="font-semibold">{creator.name}</h2>
          <p className="text-xs text-gray-500">@{creator.slug}</p>
        </div>

        {creator.description && (
          <p className="line-clamp-2 text-sm text-gray-400">{creator.description}</p>
        )}

        {creator.tags?.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {creator.tags.map((t) => (
              <span
                key={t}
                className="rounded-full bg-gray-800 px-2 py-0.5 text-xs text-gray-400"
              >
                {t}
              </span>
            ))}
          </div>
        )}

        <div className="mt-auto flex gap-2 pt-2">
          <Link
            href={`/creators/${creator.slug}`}
            className="flex-1 rounded-lg border border-gray-700 py-1.5 text-center text-sm hover:bg-gray-800 transition-colors"
          >
            Edit
          </Link>
          <button
            onClick={() => onDelete(creator.slug)}
            className="rounded-lg border border-red-900 px-3 py-1.5 text-sm text-red-400 hover:bg-red-900/30 transition-colors"
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  );
}
