'use client';

/**
 * Admin Jobs board — visibility + control over the durable run engine.
 *
 * Reads at a glance: status counters across the top, filter pills below,
 * one row per job. Click any row to slide a detail drawer in from the
 * right with the full payload, result, and error — including the run_id
 * linked to the run detail page for the full agent trace.
 *
 * Operator actions:
 *   * Per-row Cancel (queued/running) and Retry (dead/failed/cancelled).
 *   * Bulk "Retry all dead" — drains the DLQ in one click after a
 *     platform outage. Confirmation required because it can flood the
 *     queue if you haven't actually fixed the root cause.
 *   * Force orphan sweep — promotes stale `running` jobs back to
 *     `queued` for re-claiming.
 *
 * Refresh: explicit Refresh button + 5s polling (toggleable). Polling
 * pauses while the detail drawer is open so the row you're inspecting
 * doesn't disappear from under you.
 */
import { useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api/client';
import { toast } from '@/components/ui/Toast';
import { AppShell } from '@/components/layout/AppShell';
import {
  RefreshCw, XCircle, RotateCcw, AlertTriangle, Clock,
  CheckCircle2, Activity, Search, Inbox, RefreshCcw, ExternalLink, X,
} from 'lucide-react';

type Job = {
  id: string; org_id: string; kind: string; status: string;
  attempt: number; max_attempts: number; priority: number;
  scheduled_for: string | null; run_id: string | null;
  claimed_by: string | null;
  started_at: string | null; finished_at: string | null;
  error: string | null; result: any;
  idempotency_key: string | null;
  created_at: string | null; updated_at: string | null;
  payload_keys: string[]; payload?: any;
};

const STATUS_TONE: Record<string, string> = {
  queued: 'bg-blue-100 text-blue-800',
  running: 'bg-amber-100 text-amber-800',
  succeeded: 'bg-green-100 text-green-800',
  failed: 'bg-orange-100 text-orange-800',
  dead: 'bg-red-100 text-red-800',
  cancelled: 'bg-stone-200 text-stone-700',
};

export default function AdminJobsPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string>('all');
  const [kindFilter, setKindFilter] = useState<string>('all');
  const [search, setSearch] = useState<string>('');
  const [autoRefresh, setAutoRefresh] = useState<boolean>(true);
  const [selected, setSelected] = useState<Job | null>(null);
  const [bulkRetrying, setBulkRetrying] = useState(false);

  async function refresh() {
    try {
      const params = new URLSearchParams({ limit: '300' });
      if (filter !== 'all') params.set('status', filter);
      if (kindFilter !== 'all') params.set('kind', kindFilter);
      const data = await api.get<{ jobs: Job[]; counts: Record<string, number> }>(
        `/jobs?${params.toString()}`,
      );
      setJobs(data.jobs || []);
      setCounts(data.counts || {});
    } catch (e: any) {
      toast.error(`Failed to load jobs: ${e?.message || 'unknown'}`);
    } finally {
      setLoading(false);
    }
  }

  async function loadJobDetail(id: string) {
    // Full payload comes back on the per-job endpoint; the list omits
    // it to keep wire size sane.
    try {
      const full = await api.get<Job>(`/jobs/${id}`);
      setSelected(full);
    } catch (e: any) {
      toast.error(`Failed to load job: ${e?.message || 'unknown'}`);
    }
  }

  useEffect(() => {
    setLoading(true);
    refresh();
  }, [filter, kindFilter]);

  useEffect(() => {
    // Pause polling while a drawer is open so the user's selection isn't
    // wiped out mid-inspection.
    if (!autoRefresh || selected) return;
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [autoRefresh, filter, kindFilter, selected]);

  async function cancel(id: string) {
    if (!confirm('Cancel this job?')) return;
    try {
      await api.post(`/jobs/${id}/cancel`, { reason: 'admin cancel' });
      toast.success(`Cancelled ${id.slice(0, 8)}`);
      refresh();
      if (selected?.id === id) setSelected(null);
    } catch (e: any) {
      toast.error(`Cancel failed: ${e?.message || 'unknown'}`);
    }
  }

  async function retry(id: string) {
    try {
      await api.post(`/jobs/${id}/retry`);
      toast.success(`Re-queued ${id.slice(0, 8)}`);
      refresh();
    } catch (e: any) {
      toast.error(`Retry failed: ${e?.message || 'unknown'}`);
    }
  }

  async function bulkRetryDead() {
    const dead = jobs.filter(j => j.status === 'dead');
    if (dead.length === 0) {
      toast.info('No dead jobs to retry');
      return;
    }
    const ok = confirm(
      `Re-queue ${dead.length} dead job${dead.length === 1 ? '' : 's'}?\n\n`
      + 'Run only after the underlying problem is fixed — otherwise you\'re '
      + 'flooding the queue with jobs that will just die again.',
    );
    if (!ok) return;
    setBulkRetrying(true);
    let succeeded = 0;
    for (const j of dead) {
      try {
        await api.post(`/jobs/${j.id}/retry`);
        succeeded++;
      } catch {
        // Continue — surface aggregate at the end.
      }
    }
    setBulkRetrying(false);
    toast.success(`Re-queued ${succeeded}/${dead.length} dead jobs`);
    refresh();
  }

  async function sweep() {
    try {
      const data = await api.post<{ recovered: number }>('/jobs/sweep');
      toast.success(`Recovery sweep: recovered ${data.recovered}`);
      refresh();
    } catch (e: any) {
      toast.error(`Sweep failed: ${e?.message || 'unknown'}`);
    }
  }

  const kinds = useMemo(() => {
    const set = new Set<string>();
    jobs.forEach(j => set.add(j.kind));
    return ['all', ...Array.from(set).sort()];
  }, [jobs]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return jobs;
    return jobs.filter(j =>
      j.id.toLowerCase().includes(q)
      || j.kind.toLowerCase().includes(q)
      || (j.error || '').toLowerCase().includes(q)
      || (j.run_id || '').toLowerCase().includes(q),
    );
  }, [jobs, search]);

  const deadCount = counts.dead || 0;

  return (
    <AppShell>
      <div className="p-6 max-w-7xl mx-auto space-y-4">
        <header>
          <p className="text-sm text-ink-600">
            Durable workflow phases. Workers claim, retry, and dead-letter automatically.
            Click any row for the full payload + trace link.
          </p>
        </header>

        <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
          <Counter label="Queued" value={counts.queued} icon={<Clock size={14}/>} tone="blue" />
          <Counter label="Running" value={counts.running} icon={<Activity size={14}/>} tone="amber" />
          <Counter label="Succeeded" value={counts.succeeded} icon={<CheckCircle2 size={14}/>} tone="green" />
          <Counter label="Failed" value={counts.failed} icon={<AlertTriangle size={14}/>} tone="orange" />
          <Counter label="Dead" value={counts.dead} icon={<XCircle size={14}/>} tone="red" />
          <Counter label="Cancelled" value={counts.cancelled} icon={<XCircle size={14}/>} tone="gray" />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <select className="input" value={filter} onChange={e => setFilter(e.target.value)}>
            <option value="all">All statuses</option>
            <option value="queued">Queued</option>
            <option value="running">Running</option>
            <option value="succeeded">Succeeded</option>
            <option value="failed">Failed</option>
            <option value="dead">Dead</option>
            <option value="cancelled">Cancelled</option>
          </select>
          <select className="input" value={kindFilter} onChange={e => setKindFilter(e.target.value)}>
            {kinds.map(k => (
              <option key={k} value={k}>{k === 'all' ? 'All kinds' : k}</option>
            ))}
          </select>
          <div className="relative flex-1 min-w-[200px]">
            <Search size={14} className="absolute left-3 top-2.5 text-ink-400" />
            <input
              className="input pl-8 w-full"
              placeholder="Search id / run_id / error…"
              value={search} onChange={e => setSearch(e.target.value)}
            />
          </div>
          <label className="flex items-center gap-1 text-xs text-ink-600 px-2">
            <input
              type="checkbox" checked={autoRefresh}
              onChange={e => setAutoRefresh(e.target.checked)}
            />
            Auto-refresh
          </label>
          <button className="btn btn-outline" onClick={refresh} disabled={loading}>
            <RefreshCw size={14}/> Refresh
          </button>
          <button
            className="btn btn-outline"
            onClick={bulkRetryDead}
            disabled={bulkRetrying || deadCount === 0}
            title={deadCount === 0 ? 'No dead jobs' : `Re-queue ${deadCount} dead`}
          >
            <RefreshCcw size={14}/>
            {bulkRetrying ? 'Retrying…' : `Retry all dead${deadCount ? ` (${deadCount})` : ''}`}
          </button>
          <button
            className="btn btn-outline"
            onClick={sweep}
            title="Force orphan-recovery: promote stale 'running' jobs back to queued."
          >
            <Activity size={14}/> Sweep
          </button>
        </div>

        <div className="overflow-x-auto bg-white border rounded">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-ink-600 bg-stone-50">
              <tr>
                <th className="p-2">Kind</th>
                <th className="p-2">Status</th>
                <th className="p-2">Run</th>
                <th className="p-2">Attempt</th>
                <th className="p-2">Scheduled</th>
                <th className="p-2">Started</th>
                <th className="p-2">Error</th>
                <th className="p-2 w-[80px]"></th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={8} className="p-4 text-center text-ink-500">Loading…</td></tr>
              ) : filtered.length === 0 ? (
                <tr>
                  <td colSpan={8} className="p-8">
                    <EmptyState filter={filter} search={search} />
                  </td>
                </tr>
              ) : filtered.map(j => (
                <tr
                  key={j.id}
                  className="border-t hover:bg-stone-50 cursor-pointer"
                  onClick={() => loadJobDetail(j.id)}
                >
                  <td className="p-2 font-mono text-xs">{j.kind}</td>
                  <td className="p-2">
                    <span className={`px-2 py-0.5 rounded-full text-xs ${STATUS_TONE[j.status] || 'bg-stone-100 text-stone-800'}`}>
                      {j.status}
                    </span>
                  </td>
                  <td className="p-2 font-mono text-xs">
                    {j.run_id ? (
                      <a
                        href={`/runs/${j.run_id}`}
                        onClick={e => e.stopPropagation()}
                        className="text-brand-600 hover:underline inline-flex items-center gap-0.5"
                      >
                        {j.run_id.slice(0, 8)}
                        <ExternalLink size={10}/>
                      </a>
                    ) : '—'}
                  </td>
                  <td className="p-2 text-xs">{j.attempt}/{j.max_attempts}</td>
                  <td className="p-2 text-xs">
                    {j.scheduled_for ? new Date(j.scheduled_for).toLocaleTimeString() : '—'}
                  </td>
                  <td className="p-2 text-xs">
                    {j.started_at ? new Date(j.started_at).toLocaleTimeString() : '—'}
                  </td>
                  <td className="p-2 text-xs text-red-700 max-w-[300px] truncate" title={j.error || ''}>
                    {j.error || ''}
                  </td>
                  <td className="p-2 text-right space-x-1" onClick={e => e.stopPropagation()}>
                    {(j.status === 'queued' || j.status === 'running') && (
                      <button className="btn btn-ghost btn-xs" title="Cancel" onClick={() => cancel(j.id)}>
                        <XCircle size={12}/>
                      </button>
                    )}
                    {(j.status === 'dead' || j.status === 'failed' || j.status === 'cancelled') && (
                      <button className="btn btn-ghost btn-xs" title="Retry" onClick={() => retry(j.id)}>
                        <RotateCcw size={12}/>
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {selected && (
        <JobDrawer
          job={selected}
          onClose={() => setSelected(null)}
          onCancel={() => cancel(selected.id)}
          onRetry={() => retry(selected.id)}
        />
      )}
    </AppShell>
  );
}

function Counter({ label, value, icon, tone }: { label: string; value: number | undefined; icon: React.ReactNode; tone: string }) {
  const toneClasses: Record<string, string> = {
    blue: 'border-blue-200 bg-blue-50 text-blue-800',
    amber: 'border-amber-200 bg-amber-50 text-amber-800',
    green: 'border-green-200 bg-green-50 text-green-800',
    orange: 'border-orange-200 bg-orange-50 text-orange-800',
    red: 'border-red-200 bg-red-50 text-red-800',
    gray: 'border-stone-200 bg-stone-50 text-stone-700',
  };
  return (
    <div className={`rounded border p-3 ${toneClasses[tone] || ''}`}>
      <div className="flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide opacity-70 flex items-center gap-1">{icon} {label}</span>
        <span className="text-xl font-semibold">{value ?? 0}</span>
      </div>
    </div>
  );
}

function EmptyState({ filter, search }: { filter: string; search: string }) {
  const isFiltered = filter !== 'all' || !!search.trim();
  return (
    <div className="text-center text-ink-500 space-y-1">
      <Inbox size={28} className="mx-auto opacity-50"/>
      {isFiltered ? (
        <>
          <p className="text-sm font-medium">No jobs match the current filter.</p>
          <p className="text-xs">Clear the filter to see everything.</p>
        </>
      ) : (
        <>
          <p className="text-sm font-medium">No jobs in the queue.</p>
          <p className="text-xs">
            Trigger a workflow run (manually or via schedule) and you'll see
            <code className="px-1 mx-1 bg-stone-100 rounded">run.start</code>
            flow through here, followed by select / plan / tailor / execute / critique / publish.
          </p>
        </>
      )}
    </div>
  );
}

function JobDrawer({ job, onClose, onCancel, onRetry }: {
  job: Job; onClose: () => void; onCancel: () => void; onRetry: () => void;
}) {
  const canCancel = job.status === 'queued' || job.status === 'running';
  const canRetry = job.status === 'dead' || job.status === 'failed' || job.status === 'cancelled';
  return (
    <>
      <div
        className="fixed inset-0 bg-black/30 z-40"
        onClick={onClose}
        aria-hidden
      />
      <aside
        className="fixed top-0 right-0 bottom-0 w-full sm:w-[520px] bg-white border-l shadow-xl z-50 overflow-y-auto"
        role="dialog" aria-label="Job detail"
      >
        <header className="sticky top-0 bg-white border-b px-4 py-3 flex items-center justify-between">
          <div className="min-w-0">
            <p className="text-xs text-ink-500 font-mono">{job.id}</p>
            <p className="font-semibold truncate">{job.kind}</p>
          </div>
          <button className="btn btn-ghost btn-xs" onClick={onClose} aria-label="Close">
            <X size={14}/>
          </button>
        </header>

        <div className="p-4 space-y-4 text-sm">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`px-2 py-0.5 rounded-full text-xs ${STATUS_TONE[job.status] || 'bg-stone-100'}`}>
              {job.status}
            </span>
            <span className="text-xs text-ink-500">
              Attempt {job.attempt}/{job.max_attempts} · priority {job.priority}
            </span>
          </div>

          <DetailRow label="Run ID">
            {job.run_id ? (
              <a className="text-brand-600 hover:underline font-mono text-xs inline-flex items-center gap-1" href={`/runs/${job.run_id}`}>
                {job.run_id}
                <ExternalLink size={10}/>
              </a>
            ) : <span className="text-ink-400">—</span>}
          </DetailRow>
          <DetailRow label="Scheduled for">{fmtDate(job.scheduled_for)}</DetailRow>
          <DetailRow label="Started at">{fmtDate(job.started_at)}</DetailRow>
          <DetailRow label="Finished at">{fmtDate(job.finished_at)}</DetailRow>
          <DetailRow label="Claimed by">{job.claimed_by || <span className="text-ink-400">—</span>}</DetailRow>
          <DetailRow label="Idempotency key">
            <code className="text-xs">{job.idempotency_key || '—'}</code>
          </DetailRow>

          {job.error && (
            <section>
              <h3 className="text-xs font-semibold uppercase text-ink-500 mb-1">Error</h3>
              <pre className="text-xs bg-red-50 border border-red-200 text-red-800 p-2 rounded whitespace-pre-wrap break-words">{job.error}</pre>
            </section>
          )}

          <section>
            <h3 className="text-xs font-semibold uppercase text-ink-500 mb-1">Payload</h3>
            <pre className="text-xs bg-stone-50 border p-2 rounded overflow-x-auto">{JSON.stringify(job.payload ?? {}, null, 2)}</pre>
          </section>

          {job.result && Object.keys(job.result).length > 0 && (
            <section>
              <h3 className="text-xs font-semibold uppercase text-ink-500 mb-1">Result</h3>
              <pre className="text-xs bg-stone-50 border p-2 rounded overflow-x-auto">{JSON.stringify(job.result, null, 2)}</pre>
            </section>
          )}

          <div className="flex gap-2 pt-2 border-t">
            {canCancel && (
              <button className="btn btn-outline" onClick={onCancel}>
                <XCircle size={14}/> Cancel job
              </button>
            )}
            {canRetry && (
              <button className="btn btn-primary" onClick={onRetry}>
                <RotateCcw size={14}/> Re-queue
              </button>
            )}
            {job.run_id && (
              <a className="btn btn-outline ml-auto" href={`/runs/${job.run_id}`}>
                View run trace <ExternalLink size={12}/>
              </a>
            )}
          </div>
        </div>
      </aside>
    </>
  );
}

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3 text-xs">
      <span className="text-ink-500 w-32 shrink-0">{label}</span>
      <span className="text-ink-800 break-all">{children}</span>
    </div>
  );
}

function fmtDate(s: string | null) {
  if (!s) return <span className="text-ink-400">—</span>;
  try {
    return new Date(s).toLocaleString();
  } catch {
    return s;
  }
}
