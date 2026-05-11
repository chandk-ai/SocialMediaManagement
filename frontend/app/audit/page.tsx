'use client';

/**
 * Audit log page — cursor-paged read of ``smms.audit_log`` with
 * server-side filters.
 *
 * Why cursor (not offset) pagination
 * ──────────────────────────────────
 * The audit log is append-only and grows constantly. With offset
 * pagination, a new row arriving between "load page 1" and "load
 * page 2" would shift everything down by one — the user would see
 * the same row twice (or miss one entirely). Cursor pagination uses
 * the prior page's last ``(occurred_at, id)`` tuple as the boundary,
 * so concurrent writes don't affect what we've already shown.
 *
 * Filter state lives in URL params so a link shared with a teammate
 * lands them on the same filtered view (great for incident ops:
 * "look at /audit?action=publish&since=2026-05-01T00:00").
 *
 * The page also renders the chain-verification badge in the header —
 * a single read of /audit-logs/verify that confirms nobody has
 * tampered with the rows directly in the database (SOC2 hook).
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { AppShell } from '@/components/layout/AppShell';
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Input } from '@/components/ui/Input';
import { Spinner, InlineLoader } from '@/components/ui/Spinner';
import { api, useApi, ApiError } from '@/lib/api/client';
import { formatDateTime } from '@/lib/utils';
import {
  ShieldCheck, AlertTriangle, ScrollText, Search, X,
  RefreshCw, ChevronDown, ChevronRight, Calendar, Filter, Download,
} from 'lucide-react';

// ── types ────────────────────────────────────────────────────────────
type AuditEvent = {
  id: string;
  occurred_at: string;
  actor_type: string;
  actor_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  request_id: string | null;
};

type PagedResponse = {
  events: AuditEvent[];
  next_cursor: string | null;
  has_more: boolean;
  total_fetched: number;
};

type FilterValues = {
  actions: string[];
  resource_types: string[];
  actor_ids: string[];
};

type Filters = {
  action: string;
  actor_id: string;
  resource_type: string;
  resource_id: string;
  since: string;
  until: string;
};

const EMPTY_FILTERS: Filters = {
  action: '', actor_id: '', resource_type: '',
  resource_id: '', since: '', until: '',
};

// ── date-range presets ───────────────────────────────────────────────
const PRESETS: Array<{ label: string; days: number | null }> = [
  { label: 'Last 24h',  days: 1 },
  { label: 'Last 7d',   days: 7 },
  { label: 'Last 30d',  days: 30 },
  { label: 'Last 90d',  days: 90 },
  { label: 'All time',  days: null },
];


// ─────────────────────────────────────────────────────────────────────
export default function AuditPage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Filter state — initialised from URL params so deep links work.
  const [filters, setFilters] = useState<Filters>(() => ({
    action:        searchParams?.get('action')        || '',
    actor_id:      searchParams?.get('actor_id')      || '',
    resource_type: searchParams?.get('resource_type') || '',
    resource_id:   searchParams?.get('resource_id')   || '',
    since:         searchParams?.get('since')         || '',
    until:         searchParams?.get('until')         || '',
  }));

  // Pagination state — events accumulate as the user clicks "Load
  // more". A fresh filter change resets these.
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  // Filter-value dropdowns — populated from /audit-logs/filters so
  // we only offer values an org has actually produced.
  const { data: filterValues } = useApi<FilterValues>(
    '/audit-logs/filters?days=90',
  );

  // Chain-verification badge (admin-only on the server; non-admins
  // get a 403 which useApi surfaces as error and we just hide it).
  const { data: chain } = useApi<{
    chain_intact: boolean; ok: number; tampered: number; total: number;
  }>('/audit-logs/verify?limit=1000');

  // Build the query string the API expects. Memoised so the fetch
  // useEffect doesn't re-fire on identical re-renders.
  const queryString = useMemo(() => {
    const p = new URLSearchParams();
    p.set('limit', '50');
    for (const [k, v] of Object.entries(filters)) {
      if (v.trim()) p.set(k, v.trim());
    }
    return p.toString();
  }, [filters]);

  // Sync the active filters back into the URL so the address bar is
  // shareable. Using replace (not push) so the back button doesn't
  // step through every keystroke in the filter inputs.
  useEffect(() => {
    const url = `/audit${queryString ? `?${queryString.replace(/(^|&)limit=\d+/, '')}` : ''}`
      .replace('?&', '?');
    router.replace(url, { scroll: false });
  }, [queryString, router]);

  // Initial load + reload whenever filters change.
  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    setExpanded(new Set());
    try {
      const data = await api.get<PagedResponse>(`/audit-logs/paged?${queryString}`);
      setEvents(data.events || []);
      setCursor(data.next_cursor);
      setHasMore(data.has_more);
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : (e as Error).message;
      setError(msg || 'Failed to load audit log');
      setEvents([]); setCursor(null); setHasMore(false);
    } finally {
      setLoading(false);
    }
  }, [queryString]);

  useEffect(() => { reload(); }, [reload]);

  // "Load more" — appends to the existing list rather than replacing.
  async function loadMore() {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const params = new URLSearchParams(queryString);
      params.set('cursor', cursor);
      const data = await api.get<PagedResponse>(`/audit-logs/paged?${params.toString()}`);
      setEvents(prev => [...prev, ...(data.events || [])]);
      setCursor(data.next_cursor);
      setHasMore(data.has_more);
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : (e as Error).message;
      setError(msg || 'Failed to load more');
    } finally {
      setLoadingMore(false);
    }
  }

  function setFilter<K extends keyof Filters>(k: K, v: Filters[K]) {
    setFilters(prev => ({ ...prev, [k]: v }));
  }

  function applyPreset(days: number | null) {
    if (days === null) {
      setFilter('since', ''); setFilter('until', '');
    } else {
      const since = new Date();
      since.setDate(since.getDate() - days);
      // Trim to minute precision — seconds-level filters are noise
      // on a date picker and the underlying index doesn't care.
      setFilter('since', since.toISOString().slice(0, 16));
      setFilter('until', '');
    }
  }

  function clearAll() {
    setFilters(EMPTY_FILTERS);
  }

  const anyFilterActive = Object.values(filters).some(v => v.trim());

  // CSV export — exports the currently-visible event window. Honest
  // about scope: it's not the full log unless the user paged through.
  function exportCsv() {
    const headers = ['occurred_at', 'actor_id', 'action', 'resource_type', 'resource_id', 'request_id'];
    const rows = events.map(e => [
      e.occurred_at, e.actor_id || '', e.action,
      e.resource_type, e.resource_id || '', e.request_id || '',
    ]);
    const csv = [headers, ...rows]
      .map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(','))
      .join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `audit-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <AppShell>
      <div className="p-6 max-w-7xl mx-auto space-y-5">
        <header className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-ink-900 flex items-center gap-2">
              <ScrollText size={20} className="text-ink-500" />
              Audit log
            </h1>
            <p className="text-sm text-ink-600 mt-0.5">
              Tamper-evident timeline of every state-changing action. Cursor-paged
              so a busy log doesn't shift rows under you.
            </p>
          </div>
          {chain && (
            <ChainBadge
              intact={chain.chain_intact}
              ok={chain.ok}
              tampered={chain.tampered}
              total={chain.total}
            />
          )}
        </header>

        {/* ── Filters ─────────────────────────────────────────────── */}
        <Card className="p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold flex items-center gap-2">
              <Filter size={14} className="text-ink-500" />
              Filters
            </h2>
            <div className="flex gap-2">
              {anyFilterActive && (
                <button className="btn btn-ghost btn-xs" onClick={clearAll}>
                  <X size={12} /> Clear
                </button>
              )}
              <button className="btn btn-outline btn-xs" onClick={reload} disabled={loading}>
                <RefreshCw size={12} /> Refresh
              </button>
              <button
                className="btn btn-outline btn-xs"
                onClick={exportCsv}
                disabled={events.length === 0}
                title="Export the currently loaded events as CSV"
              >
                <Download size={12} /> Export
              </button>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
            {/* Action — substring search */}
            <div>
              <Label>Action</Label>
              <div className="relative">
                <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-400" />
                <Input
                  className="pl-7"
                  placeholder="e.g. publish, llm_key"
                  value={filters.action}
                  onChange={e => setFilter('action', e.target.value)}
                  list="audit-actions"
                />
                <datalist id="audit-actions">
                  {filterValues?.actions?.map(a => <option key={a} value={a} />)}
                </datalist>
              </div>
            </div>

            {/* Resource type — dropdown of distinct values */}
            <div>
              <Label>Resource type</Label>
              <select
                className="input"
                value={filters.resource_type}
                onChange={e => setFilter('resource_type', e.target.value)}
              >
                <option value="">All resources</option>
                {filterValues?.resource_types?.map(rt => (
                  <option key={rt} value={rt}>{rt}</option>
                ))}
              </select>
            </div>

            {/* Actor — UUID picker (we show the short form) */}
            <div>
              <Label>Actor</Label>
              <select
                className="input"
                value={filters.actor_id}
                onChange={e => setFilter('actor_id', e.target.value)}
              >
                <option value="">All actors</option>
                {filterValues?.actor_ids?.map(id => (
                  <option key={id} value={id}>{id.slice(0, 8)}…</option>
                ))}
              </select>
            </div>

            {/* Resource ID — free text, gets validated as UUID server-side */}
            <div>
              <Label>Resource ID</Label>
              <Input
                placeholder="UUID"
                value={filters.resource_id}
                onChange={e => setFilter('resource_id', e.target.value)}
              />
            </div>
          </div>

          {/* Date-range presets + custom range */}
          <div className="mt-4 flex flex-wrap items-end gap-3">
            <div className="flex flex-wrap gap-1.5">
              {PRESETS.map(p => (
                <button
                  key={p.label}
                  className="btn btn-ghost btn-xs"
                  onClick={() => applyPreset(p.days)}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <div className="flex items-end gap-2">
              <div>
                <Label>From</Label>
                <input
                  type="datetime-local"
                  className="input"
                  value={filters.since}
                  onChange={e => setFilter('since', e.target.value)}
                />
              </div>
              <div>
                <Label>To</Label>
                <input
                  type="datetime-local"
                  className="input"
                  value={filters.until}
                  onChange={e => setFilter('until', e.target.value)}
                />
              </div>
            </div>
          </div>
        </Card>

        {/* ── Events table ────────────────────────────────────────── */}
        <Card>
          <div className="px-4 py-2.5 border-b border-ink-100 flex items-center justify-between">
            <p className="text-xs text-ink-500">
              {loading ? 'Loading…'
                : events.length === 0 ? 'No events'
                : <>
                    Showing <strong className="text-ink-800 tabular">{events.length}</strong>
                    {hasMore && <> · <em className="text-ink-400">more available</em></>}
                  </>}
            </p>
            {loading && <Spinner size="xs" />}
          </div>

          {error && (
            <div className="px-4 py-3 text-sm text-red-700 bg-red-50 border-b border-red-200 flex items-start gap-2">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <div>
                <p className="font-medium">Couldn't load audit events</p>
                <p className="text-xs text-red-600 mt-0.5">{error}</p>
                <p className="text-xs text-red-500 mt-1">
                  If you're running the in-memory backend in dev, the audit
                  service isn't bound — that's expected.
                </p>
              </div>
            </div>
          )}

          {!loading && !error && events.length === 0 && (
            <div className="p-10 text-center text-ink-500">
              <ScrollText size={24} className="mx-auto opacity-50 mb-2" />
              <p className="text-sm font-medium">No events match the current filter.</p>
              {anyFilterActive && (
                <button className="btn btn-ghost btn-xs mt-2" onClick={clearAll}>
                  Clear filters
                </button>
              )}
            </div>
          )}

          {events.length > 0 && (
            <ul className="divide-y divide-ink-100">
              {events.map(e => (
                <EventRow
                  key={e.id}
                  event={e}
                  expanded={expanded.has(e.id)}
                  onToggle={() => setExpanded(prev => {
                    const next = new Set(prev);
                    if (next.has(e.id)) next.delete(e.id); else next.add(e.id);
                    return next;
                  })}
                  onFilterAction={() => setFilter('action', e.action)}
                  onFilterActor={() => e.actor_id && setFilter('actor_id', e.actor_id)}
                />
              ))}
            </ul>
          )}

          {hasMore && (
            <div className="p-4 border-t border-ink-100 flex justify-center">
              <button
                className="btn btn-outline btn-sm"
                onClick={loadMore}
                disabled={loadingMore}
              >
                {loadingMore ? <InlineLoader label="Loading more" /> : (
                  <>Load 50 more <ChevronDown size={12} /></>
                )}
              </button>
            </div>
          )}
        </Card>
      </div>
    </AppShell>
  );
}


// ── pieces ───────────────────────────────────────────────────────────
function Label({ children }: { children: React.ReactNode }) {
  return (
    <label className="block text-[10px] uppercase tracking-wider text-ink-500 mb-1">
      {children}
    </label>
  );
}


function ChainBadge({ intact, ok, tampered, total }: {
  intact: boolean; ok: number; tampered: number; total: number;
}) {
  return (
    <div
      className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs ${
        intact
          ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
          : 'border-red-200 bg-red-50 text-red-800'
      }`}
      title={intact
        ? 'Cryptographic chain verified — nothing has been tampered with directly in the database.'
        : `${tampered} of ${total} rows fail verification.`}
    >
      {intact
        ? <ShieldCheck size={14} className="text-emerald-600" />
        : <AlertTriangle size={14} className="text-red-600" />}
      <span className="font-medium">
        {intact ? 'Chain intact' : 'Chain compromised'}
      </span>
      <span className="text-ink-500 tabular">
        · {ok}/{total} verified
      </span>
    </div>
  );
}


function EventRow({ event, expanded, onToggle, onFilterAction, onFilterActor }: {
  event: AuditEvent;
  expanded: boolean;
  onToggle: () => void;
  onFilterAction: () => void;
  onFilterActor: () => void;
}) {
  const tone = toneFor(event.action);
  return (
    <li className="hover:bg-ink-50/50 transition-colors">
      <button
        onClick={onToggle}
        className="w-full px-4 py-2.5 flex items-start gap-3 text-left"
      >
        <span className="mt-0.5 text-ink-400 shrink-0">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
        <div className="flex-1 min-w-0 grid grid-cols-12 gap-3 items-center">
          <span className="col-span-3 text-xs text-ink-500 tabular truncate">
            <Calendar size={11} className="inline mr-1 text-ink-400" />
            {formatDateTime(event.occurred_at)}
          </span>
          <span className="col-span-4 text-sm font-medium text-ink-900 truncate"
                title={event.action}>
            <Badge tone={tone}>{event.action}</Badge>
          </span>
          <span className="col-span-3 text-xs text-ink-600 truncate"
                title={`${event.resource_type}${event.resource_id ? ` · ${event.resource_id}` : ''}`}>
            {event.resource_type}
            {event.resource_id && (
              <span className="text-ink-400 font-mono"> · {event.resource_id.slice(0, 8)}…</span>
            )}
          </span>
          <span className="col-span-2 text-xs text-ink-500 truncate"
                title={event.actor_id || event.actor_type}>
            {event.actor_type === 'user' && event.actor_id
              ? <span className="font-mono">{event.actor_id.slice(0, 8)}…</span>
              : <em>{event.actor_type}</em>}
          </span>
        </div>
      </button>

      {expanded && (
        <div className="px-12 pb-4 -mt-1 space-y-3">
          <div className="flex gap-2 text-[11px]">
            <button
              className="btn btn-ghost btn-xs"
              onClick={(ev) => { ev.stopPropagation(); onFilterAction(); }}
              title="Show only events with this action"
            >
              Filter to this action
            </button>
            {event.actor_id && (
              <button
                className="btn btn-ghost btn-xs"
                onClick={(ev) => { ev.stopPropagation(); onFilterActor(); }}
                title="Show only events from this actor"
              >
                Filter to this actor
              </button>
            )}
            {event.request_id && (
              <span className="text-ink-500 self-center">
                req: <code className="font-mono text-[10px]">{event.request_id}</code>
              </span>
            )}
          </div>

          {(event.before || event.after) && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {event.before && <JsonBlock label="Before" data={event.before} tone="muted" />}
              {event.after  && <JsonBlock label="After"  data={event.after}  tone="accent" />}
            </div>
          )}
        </div>
      )}
    </li>
  );
}


function JsonBlock({ label, data, tone }: {
  label: string; data: unknown; tone: 'muted' | 'accent';
}) {
  const cls = tone === 'accent'
    ? 'border-accent-muted bg-accent-muted/40'
    : 'border-ink-200 bg-ink-50';
  return (
    <div className={`rounded-lg border ${cls} overflow-hidden`}>
      <div className="px-2.5 py-1 text-[10px] uppercase tracking-wider text-ink-500 border-b border-ink-200/60">
        {label}
      </div>
      <pre className="text-[11px] leading-relaxed text-ink-800 p-2.5 overflow-x-auto max-h-72">
{JSON.stringify(data, null, 2)}
      </pre>
    </div>
  );
}


// Action → badge tone. Heuristic on the verb suffix.
function toneFor(action: string): 'success' | 'danger' | 'warning' | 'info' | 'default' {
  const a = action.toLowerCase();
  if (/(publish|approve|connect|create|grant)$/.test(a)) return 'success';
  if (/(delete|revoke|disconnect|cancel|fail|dlq|deny)$/.test(a)) return 'danger';
  if (/(reset|edit|update|set|configure|escalate)$/.test(a)) return 'warning';
  return 'info';
}
