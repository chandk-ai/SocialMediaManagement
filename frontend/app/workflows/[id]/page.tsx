'use client';
/**
 * Workflow Detail page — the place users land when they click "Details" on
 * a workflow card. Shows everything in one screen: configuration, recent
 * runs (with full agent trace), posts produced by this workflow.
 *
 * Designed for the "what happened?" moment after a run fails or doesn't
 * publish what was expected.
 */
import { useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { mutate } from 'swr';

import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { useApi, api, ApiError } from '@/lib/api/client';
import { ApiErrorBanner } from '@/components/ui/ApiErrorBanner';
import type { Workflow, Source, PlatformGroup, Post } from '@/lib/api/types';
import {
  ArrowLeft, Play, Pause, AlertTriangle, CheckCircle2, XCircle,
  Clock, ChevronDown, ChevronRight,
} from 'lucide-react';
import { formatDateTime } from '@/lib/utils';

type RunDetail = {
  id: string;
  workflow_id: string;
  status: string;
  directive: string;
  initiator: string | null;
  revision_count: number;
  started_at: string;
  finished_at: string | null;
  error: string | null;
  trace: Array<{ agent: string; event: string; ts: string } & Record<string, unknown>>;
};

export default function WorkflowDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: workflows, error: wfErr } = useApi<Workflow[]>('/workflows');
  const { data: runs } = useApi<RunDetail[]>(`/workflows/${id}/runs?limit=20`);
  const { data: posts } = useApi<Post[]>(`/workflows/${id}/posts?limit=50`);
  const { data: sources } = useApi<Source[]>('/sources');
  const { data: groups } = useApi<PlatformGroup[]>('/platforms/grouped');

  const workflow = (workflows ?? []).find(w => w.id === id);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function call(action: string, fn: () => Promise<unknown>) {
    setBusy(action); setErr(null);
    try {
      await fn();
      await mutate('/workflows');
      await mutate(`/workflows/${id}/runs?limit=20`);
      await mutate(`/workflows/${id}/posts?limit=50`);
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Action failed.');
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title={workflow?.name ?? 'Workflow'} />
        <main className="flex-1 overflow-y-auto p-6 space-y-4">
          <Link href="/workflows" className="inline-flex items-center gap-1 text-sm text-ink-500 hover:text-ink-700">
            <ArrowLeft size={14} /> Back to workflows
          </Link>

          <ApiErrorBanner error={wfErr} retry={() => mutate('/workflows')} />

          {!workflow ? (
            <Card>
              <CardTitle>Loading…</CardTitle>
            </Card>
          ) : (
            <>
              {/* Header card with quick actions */}
              <Card>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <CardTitle>{workflow.name}</CardTitle>
                    <CardDescription>{workflow.description || <em className="text-ink-400">no description</em>}</CardDescription>
                  </div>
                  <Badge tone={
                    workflow.status === 'active' ? 'success' :
                    workflow.status === 'paused' ? 'warning' : 'default'
                  }>{workflow.status}</Badge>
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Button size="sm"
                          onClick={() => call('run', () => api.post(`/workflows/${id}/run`))}
                          disabled={busy !== null}>
                    <Play size={14} /> {busy === 'run' ? 'Running…' : 'Run now'}
                  </Button>
                  {workflow.status === 'active' ? (
                    <Button size="sm" variant="outline"
                            onClick={() => call('pause', () => api.post(`/workflows/${id}/pause`))}>
                      <Pause size={14} /> Pause
                    </Button>
                  ) : (
                    <Button size="sm" variant="outline"
                            onClick={() => call('activate', () => api.post(`/workflows/${id}/activate`))}>
                      <Play size={14} /> Activate
                    </Button>
                  )}
                </div>
                {err && (
                  <div className="mt-3 flex items-start gap-2 text-xs text-red-800 bg-red-50 border border-red-200 rounded p-2">
                    <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                    <span>{err}</span>
                  </div>
                )}
              </Card>

              {/* Configuration summary */}
              <Card>
                <CardTitle>Configuration</CardTitle>
                <dl className="mt-3 grid grid-cols-1 md:grid-cols-[160px_1fr] gap-y-2 text-sm">
                  <dt className="text-ink-500">Sources</dt>
                  <dd>{sourceNames(workflow, sources ?? []) || <em className="text-ink-400">none — chat-driven</em>}</dd>
                  <dt className="text-ink-500">Target accounts</dt>
                  <dd>{platformLabels(workflow, groups ?? []) || <em className="text-red-600">none configured</em>}</dd>
                  <dt className="text-ink-500">Schedule</dt>
                  <dd>{describeSchedule(workflow)}</dd>
                  <dt className="text-ink-500">Tone / audience</dt>
                  <dd>{workflow.config?.tone}{workflow.config?.audience ? ` · ${workflow.config.audience}` : ''}</dd>
                  <dt className="text-ink-500">Human approval</dt>
                  <dd>{(workflow.config as any)?.require_human_approval ? 'Required' : 'Auto-publish on approve'}</dd>
                </dl>
              </Card>

              {/* Recent runs */}
              <Card>
                <CardTitle>Recent runs</CardTitle>
                <CardDescription>
                  The agents' step-by-step trace for each execution. Expand a run to see what each agent did.
                </CardDescription>
                {runs && runs.length === 0 ? (
                  <div className="mt-4 text-sm text-ink-500 italic">
                    No runs yet. Click "Run now" above to start one.
                  </div>
                ) : (
                  <div className="mt-3 space-y-2">
                    {(runs ?? []).map((r) => <RunRow key={r.id} run={r} />)}
                  </div>
                )}
              </Card>

              {/* Posts produced */}
              <Card>
                <CardTitle>Posts</CardTitle>
                <CardDescription>
                  Drafts and published posts from this workflow.
                </CardDescription>
                {posts && posts.length === 0 ? (
                  <div className="mt-4 text-sm text-ink-500 italic">
                    No posts yet — they'll appear here once a run completes.
                  </div>
                ) : (
                  <div className="mt-3 space-y-2">
                    {(posts ?? []).map((p) => (
                      <div key={p.id} className="border border-ink-100 rounded-lg p-3 text-sm">
                        <div className="flex items-start justify-between gap-2">
                          <Badge tone={
                            p.status === 'published' ? 'success' :
                            p.status === 'failed' ? 'danger' :
                            p.status === 'review' ? 'warning' : 'default'
                          }>{p.status}</Badge>
                          <span className="text-xs text-ink-500">{formatDateTime(p.created_at)}</span>
                        </div>
                        <p className="mt-2 text-ink-900 whitespace-pre-line line-clamp-3">{p.text}</p>
                        {p.error && (
                          <div className="mt-2 text-[11px] text-red-700">⚠ {p.error}</div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </Card>
            </>
          )}
        </main>
      </div>
    </div>
  );
}

function RunRow({ run }: { run: RunDetail }) {
  const [open, setOpen] = useState(false);
  const dot =
    run.status === 'succeeded' ? <CheckCircle2 size={14} className="text-emerald-600" /> :
    run.status === 'failed' ? <XCircle size={14} className="text-red-600" /> :
    run.status === 'awaiting_review' ? <Clock size={14} className="text-amber-600" /> :
    <Clock size={14} className="text-ink-400" />;
  return (
    <div className="border border-ink-100 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 text-sm text-left hover:bg-ink-50"
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {dot}
        <span className="font-medium">{run.status}</span>
        <span className="text-xs text-ink-500">{formatDateTime(run.started_at)}</span>
        {run.directive && (
          <span className="text-xs text-ink-500 italic ml-2 truncate flex-1">"{run.directive}"</span>
        )}
        {run.revision_count > 0 && (
          <span className="text-xs text-ink-500">· {run.revision_count} revision{run.revision_count !== 1 && 's'}</span>
        )}
      </button>
      {open && (
        <div className="px-3 py-2 bg-ink-50 border-t border-ink-100 text-xs">
          {run.error && (
            <div className="mb-2 text-red-700 font-medium">Error: {run.error}</div>
          )}
          {run.trace.length === 0 ? (
            <div className="italic text-ink-500">No trace recorded.</div>
          ) : (
            <ol className="space-y-1 font-mono">
              {run.trace.map((ev, i) => (
                <li key={i} className="grid grid-cols-[80px_120px_1fr] gap-2">
                  <span className="text-ink-400">{new Date(ev.ts).toLocaleTimeString()}</span>
                  <span className="text-accent">{ev.agent}</span>
                  <span>
                    <span className="font-semibold">{ev.event}</span>
                    {Object.entries(ev)
                      .filter(([k]) => !['agent', 'event', 'ts'].includes(k))
                      .slice(0, 4)
                      .map(([k, v]) => (
                        <span key={k} className="ml-2 text-ink-500">
                          {k}=<span className="text-ink-700">{summarize(v)}</span>
                        </span>
                      ))}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </div>
  );
}

function summarize(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v.length > 60 ? v.slice(0, 57) + '…' : v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  try { return JSON.stringify(v).slice(0, 60); } catch { return '?'; }
}

function sourceNames(w: Workflow, sources: Source[]): string {
  return w.source_ids
    .map(id => sources.find(s => s.id === id)?.display_name)
    .filter(Boolean)
    .join(', ');
}

function platformLabels(w: Workflow, groups: PlatformGroup[]): string {
  const accounts = groups.flatMap(g => g.accounts);
  return w.platform_ids
    .map(id => {
      const a = accounts.find(x => x.id === id);
      return a ? `${a.display_name} (${a.plugin_name})` : null;
    })
    .filter(Boolean)
    .join(', ');
}

function describeSchedule(w: Workflow): string {
  const s = w.schedule;
  switch (s.kind) {
    case 'manual': return 'Manual (run on demand)';
    case 'cron': return `Cron: ${s.cron} (${s.timezone})`;
    case 'interval': return `Every ${s.interval_minutes} minutes`;
    case 'once': return s.run_at ? `Once at ${new Date(s.run_at).toLocaleString()}` : 'Once';
    default: return s.kind;
  }
}
