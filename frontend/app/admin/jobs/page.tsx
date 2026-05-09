'use client';

/**
 * Admin Jobs board — visibility into the durable run engine.
 *
 * Shows the last N jobs grouped by status, lets an admin cancel a stuck job
 * or retry a dead one. The numbers in the header come from
 * GET /jobs/queue/health and refresh every 5 seconds.
 *
 * Designed to be the first place an operator looks when "why isn't my
 * workflow running?" comes in. Pair with the run detail page (linked by
 * run_id when present) for the full trace.
 */
import { useEffect, useMemo, useState } from 'react';
import { fetchJSON } from '@/lib/api';
import { showToast } from '@/components/Toast';
import { RefreshCw, XCircle, RotateCcw, AlertTriangle, Clock, CheckCircle2, Activity } from 'lucide-react';

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

  async function refresh() {
    try {
      const params = new URLSearchParams({ limit: '300' });
      if (filter !== 'all') params.set('status', filter);
      if (kindFilter !== 'all') params.set('kind', kindFilter);
      const data = await fetchJSON(`/api/v1/jobs?${params.toString()}`);
      setJobs(data.jobs || []);
      setCounts(data.counts || {});
    } catch (e: any) {
      showToast({ title: 'Failed to load jobs', body: e?.message || 'unknown', tone: 'error' });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setLoading(true);
    refresh();
  }, [filter, kindFilter]);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [autoRefresh, filter, kindFilter]);

  async function cancel(id: string) {
    if (!confirm('Cancel this job?')) return;
    try {
      await fetchJSON(`/api/v1/jobs/${id}/cancel`, { method: 'POST', body: JSON.stringify({ reason: 'admin cancel' }) });
      showToast({ title: 'Cancelled', body: id.slice(0, 8), tone: 'success' });
      refresh();
    } catch (e: any) {
      showToast({ title: 'Cancel failed', body: e?.message || 'unknown', tone: 'error' });
    }
  }

  async function retry(id: string) {
    try {
      await fetchJSON(`/api/v1/jobs/${id}/retry`, { method: 'POST' });
      showToast({ title: 'Re-queued', body: id.slice(0, 8), tone: 'success' });
      refresh();
    } catch (e: any) {
      showToast({ title: 'Retry failed', body: e?.message || 'unknown', tone: 'error' });
    }
  }

  async function sweep() {
    try {
      const data = await fetchJSON('/api/v1/jobs/sweep', { method: 'POST' });
      showToast({ title: 'Recovery sweep complete', body: `Recovered ${data.recovered}`, tone: 'success' });
      refresh();
    } catch (e: any) {
      showToast({ title: 'Sweep failed', body: e?.message || 'unknown', tone: 'error' });
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
      j.id.toLowerCase().includes(q) ||
      j.kind.toLowerCase().includes(q) ||
      (j.error || '').toLowerCase().includes(q) ||
      (j.run_id || '').toLowerCase().includes(q),
    );
  }, [jobs, search]);

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-4">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Job queue</h1>
          <p className="text-sm text-ink-600">Durable workflow phases. Workers claim, retry, and dead-letter automatically.</p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1 text-xs text-ink-600">
            <input type="checkbox" checked={autoRefresh} onChange={e => setAutoRefresh(e.target.checked)} />
            Auto-refresh 5s
          </label>
          <button className="btn btn-outline" onClick={refresh}>
            <RefreshCw size={14} /> Refresh
          </button>
          <button className="btn btn-outline" onClick={sweep} title="Recover orphaned 'running' jobs whose worker crashed">
            <Activity size={14} /> Sweep
          </button>
        </div>
      </header>

      <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
        <Counter label="Queued" value={counts.queued} icon={<Clock size={14} />} tone="blue" />
        <Counter label="Running" value={counts.running} icon={<Activity size={14} />} tone="amber" />
        <Counter label="Succeeded" value={counts.succeeded} icon={<CheckCircle2 size={14} />} tone="green" />
        <Counter label="Failed" value={counts.failed} icon={<AlertTriangle size={14} />} tone="orange" />
        <Counter label="Dead" value={counts.dead} icon={<XCircle size={14} />} tone="red" />
        <Counter label="Cancelled" value={counts.cancelled} icon={<XCircle size={14} />} tone="gray" />
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
          {kinds.map(k => <option key={k} value={k}>{k === 'all' ? 'All kinds' : k}</option>)}
        </select>
        <input className="input flex-1 min-w-[200px]" placeholder="Search id / run_id / error…" value={search} onChange={e => setSearch(e.target.value)} />
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-ink-600">
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
              <tr><td colSpan={8} className="p-4 text-center text-ink-500">No jobs match.</td></tr>
            ) : filtered.map(j => (
              <tr key={j.id} className="border-t hover:bg-stone-50">
                <td className="p-2 font-mono text-xs">{j.kind}</td>
                <td className="p-2">
                  <span className={`px-2 py-0.5 rounded-full text-xs ${STATUS_TONE[j.status] || 'bg-stone-100 text-stone-800'}`}>{j.status}</span>
                </td>
                <td className="p-2 font-mono text-xs">
                  {j.run_id ? <a className="text-brand-600 hover:underline" href={`/runs/${j.run_id}`}>{j.run_id.slice(0, 8)}</a> : '—'}
                </td>
                <td className="p-2 text-xs">{j.attempt}/{j.max_attempts}</td>
                <td className="p-2 text-xs">{j.scheduled_for ? new Date(j.scheduled_for).toLocaleTimeString() : '—'}</td>
                <td className="p-2 text-xs">{j.started_at ? new Date(j.started_at).toLocaleTimeString() : '—'}</td>
                <td className="p-2 text-xs text-red-700 max-w-[300px] truncate" title={j.error || ''}>{j.error || ''}</td>
                <td className="p-2 text-right space-x-1">
                  {(j.status === 'queued' || j.status === 'running') && (
                    <button className="btn btn-ghost btn-xs" title="Cancel" onClick={() => cancel(j.id)}>
                      <XCircle size={12} />
                    </button>
                  )}
                  {(j.status === 'dead' || j.status === 'failed' || j.status === 'cancelled') && (
                    <button className="btn btn-ghost btn-xs" title="Retry" onClick={() => retry(j.id)}>
                      <RotateCcw size={12} />
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
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
