'use client';
/**
 * Source detail — consumption history view.
 *
 * Shows every (source, external_id) the system has seen plus its
 * consumption status. Admins can:
 *   - Tag items (used by future custom selection strategies)
 *   - Reset a consumed/skipped item back to 'new' so a future run
 *     re-processes it (operator escape hatch)
 *
 * The list is read-mostly with a status filter, an external_id /
 * title search, and a one-click "Reset" button per row. Tag editing is
 * in a small inline popover so the user doesn't have to navigate away.
 */
import Link from 'next/link';
import { useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import { mutate } from 'swr';

import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { useApi, api, ApiError } from '@/lib/api/client';
import {
  ArrowLeft, ExternalLink, RefreshCw, Tag as TagIcon, AlertTriangle,
  CheckCircle2, Clock, X,
} from 'lucide-react';
import { formatDateTime } from '@/lib/utils';

type Item = {
  id: string;
  external_id: string;
  title: string;
  url: string | null;
  status: 'new' | 'consumed' | 'skipped' | 'expired';
  tags: string[];
  first_seen_at: string | null;
  last_seen_at: string | null;
  item_published_at: string | null;
  consumed_at: string | null;
  consumed_by_post_id: string | null;
  skipped_reason: string | null;
};

type ItemsResp = {
  items: Item[];
  counts: Record<'new' | 'consumed' | 'skipped' | 'expired', number>;
  total: number;
};

const STATUS_TONES: Record<Item['status'], 'default' | 'success' | 'warning' | 'danger'> = {
  new: 'default',
  consumed: 'success',
  skipped: 'warning',
  expired: 'danger',
};

const STATUSES: Array<Item['status'] | 'all'> = ['all', 'new', 'consumed', 'skipped'];

export default function SourceItemsPage() {
  const params = useParams();
  const sourceId = params?.id as string;
  const swrKey = `/sources/${sourceId}/items?limit=500`;
  const { data, error, isLoading } = useApi<ItemsResp>(swrKey);
  const [filter, setFilter] = useState<'all' | Item['status']>('all');
  const [q, setQ] = useState('');

  const filtered = useMemo(() => {
    let rows = data?.items ?? [];
    if (filter !== 'all') rows = rows.filter(r => r.status === filter);
    const ql = q.trim().toLowerCase();
    if (ql) {
      rows = rows.filter(r =>
        r.title.toLowerCase().includes(ql)
        || r.external_id.toLowerCase().includes(ql)
        || r.tags.some(t => t.includes(ql)),
      );
    }
    return rows;
  }, [data, filter, q]);

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Source items" />
        <main className="flex-1 overflow-y-auto p-6">
          <Link href="/sources" className="inline-flex items-center gap-1 text-sm text-ink-500 hover:text-ink-700 mb-4">
            <ArrowLeft size={14} /> Back to sources
          </Link>

          <Card>
            <div className="flex items-start justify-between gap-3 flex-wrap mb-4">
              <div>
                <CardTitle>Consumption history</CardTitle>
                <CardDescription>
                  Every item the system has fetched from this source, plus
                  what happened to it. Used by the selection layer for
                  de-duplication — admins can reset an item back to 'new'
                  to re-process it on the next run.
                </CardDescription>
              </div>
              {data && (
                <div className="text-xs flex items-center gap-2">
                  <Badge tone="default">new {data.counts.new}</Badge>
                  <Badge tone="success">consumed {data.counts.consumed}</Badge>
                  <Badge tone="warning">skipped {data.counts.skipped}</Badge>
                </div>
              )}
            </div>

            {/* Filter row */}
            <div className="flex items-center gap-2 mb-3 flex-wrap">
              {STATUSES.map(s => (
                <button
                  key={s}
                  onClick={() => setFilter(s)}
                  className={`badge ${filter === s ? 'bg-accent-muted text-accent' : 'bg-ink-100 text-ink-700'}`}
                >
                  {s}
                </button>
              ))}
              <Input
                value={q}
                onChange={(e: any) => setQ(e.target.value)}
                placeholder="Filter by title, external_id, or tag…"
                className="ml-auto w-64"
              />
            </div>

            {/* Empty / error / list */}
            {error && (
              <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-3 flex items-start gap-2">
                <AlertTriangle size={14} className="mt-0.5" />
                <span>{(error as ApiError)?.detail || 'Failed to load items.'}</span>
              </div>
            )}
            {isLoading && (
              <p className="text-sm text-ink-500 py-8 text-center">Loading…</p>
            )}
            {data && filtered.length === 0 && (
              <p className="text-sm text-ink-500 py-8 text-center">
                {data.total === 0
                  ? 'No items yet. The first time a workflow runs against this source, items will appear here.'
                  : 'No items match the current filter.'}
              </p>
            )}

            <div className="divide-y divide-ink-200">
              {filtered.map(item => (
                <ItemRow key={item.id} item={item} swrKey={swrKey} />
              ))}
            </div>
          </Card>
        </main>
      </div>
    </div>
  );
}

function ItemRow({ item, swrKey }: { item: Item; swrKey: string }) {
  const [busy, setBusy] = useState<'reset' | 'tags' | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tagsOpen, setTagsOpen] = useState(false);
  const [tagsDraft, setTagsDraft] = useState(item.tags.join(', '));

  async function reset() {
    if (!confirm(`Reset "${item.title || item.external_id}" to 'new'? A future run can re-process it.`)) return;
    setBusy('reset'); setErr(null);
    try {
      await api.post(`/source-items/${item.id}/reset`);
      await mutate(swrKey);
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Reset failed.');
    } finally { setBusy(null); }
  }

  async function saveTags() {
    setBusy('tags'); setErr(null);
    try {
      const tags = tagsDraft.split(',').map(t => t.trim()).filter(Boolean);
      await api.patch(`/source-items/${item.id}/tags`, { tags });
      setTagsOpen(false);
      await mutate(swrKey);
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Tag save failed.');
    } finally { setBusy(null); }
  }

  return (
    <div className="py-3">
      <div className="flex items-start gap-3 flex-wrap">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <Badge tone={STATUS_TONES[item.status]}>{item.status}</Badge>
            <span className="text-sm font-medium truncate">
              {item.title || <span className="text-ink-400 italic">(no title)</span>}
            </span>
            {item.url && (
              <a href={item.url} target="_blank" rel="noreferrer"
                 className="text-ink-500 hover:text-ink-700">
                <ExternalLink size={12} />
              </a>
            )}
          </div>
          <div className="text-[11px] text-ink-500 mt-0.5 font-mono truncate">
            {item.external_id}
          </div>
          <div className="text-[11px] text-ink-500 mt-1 flex items-center gap-3 flex-wrap">
            <span className="inline-flex items-center gap-1">
              <Clock size={10} /> seen {formatDateTime(item.last_seen_at || item.first_seen_at || '')}
            </span>
            {item.consumed_at && (
              <span className="inline-flex items-center gap-1 text-emerald-700">
                <CheckCircle2 size={10} /> consumed {formatDateTime(item.consumed_at)}
              </span>
            )}
            {item.skipped_reason && (
              <span className="inline-flex items-center gap-1 text-amber-700" title={item.skipped_reason}>
                skipped — {item.skipped_reason.slice(0, 80)}
              </span>
            )}
            {item.tags.length > 0 && (
              <span className="inline-flex items-center gap-1">
                <TagIcon size={10} /> {item.tags.join(', ')}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1">
          <Button size="sm" variant="ghost" onClick={() => setTagsOpen(true)}>
            <TagIcon size={12} /> Tags
          </Button>
          {item.status !== 'new' && (
            <Button size="sm" variant="ghost" onClick={reset} disabled={busy !== null}>
              <RefreshCw size={12} className={busy === 'reset' ? 'animate-spin' : ''} /> Reset
            </Button>
          )}
        </div>
      </div>

      {tagsOpen && (
        <div className="mt-2 p-2 rounded border border-ink-200 bg-ink-50/40 text-xs">
          <label className="block text-ink-500 mb-1">
            Tags (comma-separated; lowercased + deduplicated server-side)
          </label>
          <div className="flex items-center gap-2">
            <Input
              value={tagsDraft}
              onChange={(e: any) => setTagsDraft(e.target.value)}
              placeholder="ready, hold, pinned"
            />
            <Button size="sm" onClick={saveTags} disabled={busy === 'tags'}>
              Save
            </Button>
            <Button size="sm" variant="ghost" onClick={() => { setTagsOpen(false); setTagsDraft(item.tags.join(', ')); }}>
              <X size={12} />
            </Button>
          </div>
        </div>
      )}

      {err && (
        <div className="mt-2 text-[11px] text-red-800 bg-red-50 border border-red-200 rounded p-2 flex items-start gap-2">
          <AlertTriangle size={12} className="mt-0.5" /> <span>{err}</span>
        </div>
      )}
    </div>
  );
}
