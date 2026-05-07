'use client';
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input, Textarea } from '@/components/ui/Input';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { useApi, api, ApiError } from '@/lib/api/client';
import type { Workflow, Source, PlatformGroup } from '@/lib/api/types';
import { WORKFLOW_TEMPLATES, type WorkflowTemplate } from '@/lib/workflowTemplates';
import {
  Workflow as WorkflowIcon, Play, Pause, Edit3, Copy, Trash2, X,
  AlertTriangle, ChevronRight, Sparkles, Newspaper, Calendar, MessageSquare,
  FolderOpen, ExternalLink, CheckCircle2, XCircle, Clock,
} from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';
import { mutate } from 'swr';
import { formatDateTime } from '@/lib/utils';

const SCHEDULE_KINDS = ['manual', 'cron', 'interval', 'once'] as const;

const ICONS = {
  Newspaper, Calendar, MessageSquare, FolderOpen, Sparkles,
} as const;

export default function WorkflowsPage() {
  const { data: workflows } = useApi<Workflow[]>('/workflows');
  const { data: sources } = useApi<Source[]>('/sources');
  const { data: groups } = useApi<PlatformGroup[]>('/platforms/grouped');
  const [editing, setEditing] = useState<Workflow | null>(null);

  const isEmpty = workflows && workflows.length === 0;
  const hasSources = (sources?.length ?? 0) > 0;
  const hasPlatforms = (groups?.flatMap((g) => g.accounts).length ?? 0) > 0;

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Workflows" />
        <main className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Header with primary CTA */}
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-base font-semibold text-ink-900">All workflows</h2>
              <p className="text-xs text-ink-500 mt-0.5">
                A workflow turns a content source into posts on your platforms.
              </p>
            </div>
            <Link href="/workflows/new"><Button size="sm">+ New workflow</Button></Link>
          </div>

          {/* Smart pre-flight: missing sources / platforms */}
          {workflows && (!hasSources || !hasPlatforms) && (
            <PreflightChecklist hasSources={hasSources} hasPlatforms={hasPlatforms} />
          )}

          {/* Empty state with template gallery */}
          {isEmpty ? (
            <EmptyStateWithTemplates
              hasSources={hasSources}
              hasPlatforms={hasPlatforms}
            />
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {(workflows ?? []).map((w) => (
                <SmartWorkflowCard
                  key={w.id}
                  workflow={w}
                  onEdit={() => setEditing(w)}
                />
              ))}
            </div>
          )}
        </main>
      </div>

      {editing && (
        <EditWorkflowDialog workflow={editing} onClose={() => setEditing(null)} />
      )}
    </div>
  );
}

/* ───────── Pre-flight checklist ──────────────────────────────────────── */

function PreflightChecklist({ hasSources, hasPlatforms }: {
  hasSources: boolean;
  hasPlatforms: boolean;
}) {
  return (
    <div className="rounded-xl border border-blue-200 bg-blue-50 p-4">
      <div className="flex items-start gap-3">
        <Sparkles size={18} className="text-blue-700 shrink-0 mt-0.5" />
        <div className="flex-1">
          <div className="text-sm font-medium text-blue-900">
            Almost ready — finish setup first
          </div>
          <p className="text-xs text-blue-800/80 mt-0.5">
            Workflows need at least one source (where content comes from) and one
            connected account (where posts go).
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Link href="/sources">
              <Button size="sm" variant="outline">
                {hasSources ? <CheckCircle2 size={12} className="text-emerald-600" />
                            : <span className="text-xs">1.</span>}
                Add a source
              </Button>
            </Link>
            <Link href="/platforms">
              <Button size="sm" variant="outline">
                {hasPlatforms ? <CheckCircle2 size={12} className="text-emerald-600" />
                              : <span className="text-xs">2.</span>}
                Connect an account
              </Button>
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ───────── Empty state with template gallery ─────────────────────────── */

function EmptyStateWithTemplates({ hasSources, hasPlatforms }: {
  hasSources: boolean;
  hasPlatforms: boolean;
}) {
  return (
    <div className="space-y-4">
      <div className="text-center py-10">
        <WorkflowIcon size={36} className="mx-auto text-ink-400" />
        <h3 className="mt-3 text-base font-semibold text-ink-900">
          Build your first workflow
        </h3>
        <p className="text-sm text-ink-500 mt-1 max-w-md mx-auto">
          Start from a template — we'll prefill the schedule, tone, and platform
          mapping. You'll only need to pick which source and account to use.
        </p>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {WORKFLOW_TEMPLATES.map((t) => (
          <TemplateCard key={t.key} template={t} ready={hasSources && hasPlatforms} />
        ))}
      </div>
      <div className="text-center pt-2">
        <Link href="/workflows/new">
          <Button variant="ghost" size="sm">
            Or start from blank →
          </Button>
        </Link>
      </div>
    </div>
  );
}

function TemplateCard({ template, ready }: {
  template: WorkflowTemplate;
  ready: boolean;
}) {
  const Icon = ICONS[template.icon];
  return (
    <Link href={`/workflows/new?template=${template.key}`}>
      <Card className="hover:border-accent transition-colors cursor-pointer h-full">
        <div className="flex items-start gap-3">
          <div className="rounded-lg bg-accent-muted p-2 text-accent">
            <Icon size={18} />
          </div>
          <div className="flex-1 min-w-0">
            <CardTitle className="text-sm">{template.title}</CardTitle>
            <CardDescription className="text-xs mt-1">{template.tagline}</CardDescription>
            <div className="mt-3 flex flex-wrap gap-1">
              {template.recommendedPlatforms.map((p) => (
                <Badge key={p} tone="default">{p}</Badge>
              ))}
            </div>
            {!ready && (
              <p className="text-[11px] text-amber-700 mt-2">
                Connect a source + account first to use this template.
              </p>
            )}
          </div>
          <ChevronRight size={16} className="text-ink-400 shrink-0 mt-1" />
        </div>
      </Card>
    </Link>
  );
}

/* ───────── Smart workflow card ──────────────────────────────────────── */

function SmartWorkflowCard({ workflow, onEdit }: {
  workflow: Workflow;
  onEdit: () => void;
}) {
  const { data: runs } = useApi<RecentRun[]>(`/workflows/${workflow.id}/runs?limit=5`);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const lastRun = runs?.[0];
  const failedRecently = (runs ?? []).slice(0, 3).some((r) => r.status === 'failed');

  async function call(action: string, fn: () => Promise<unknown>) {
    setBusy(action); setErr(null);
    try {
      await fn();
      await mutate('/workflows');
      await mutate(`/workflows/${workflow.id}/runs?limit=5`);
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Action failed.');
    } finally {
      setBusy(null);
    }
  }

  const scheduleHint = formatScheduleHint(workflow);
  const isActive = workflow.status === 'active';
  const isPaused = workflow.status === 'paused';

  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <CardTitle className="truncate">{workflow.name}</CardTitle>
          <CardDescription className="truncate">
            {workflow.description || <em className="text-ink-400">no description</em>}
          </CardDescription>
        </div>
        <Badge tone={
          isActive ? 'success' : isPaused ? 'warning' :
          workflow.status === 'archived' ? 'default' : 'default'
        }>
          {workflow.status}
        </Badge>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-ink-500">
        <div>
          <span className="font-medium text-ink-700">{workflow.source_ids.length}</span> source{workflow.source_ids.length !== 1 && 's'}
          {' → '}
          <span className="font-medium text-ink-700">{workflow.platform_ids.length}</span> account{workflow.platform_ids.length !== 1 && 's'}
        </div>
        <div className="text-right">{scheduleHint}</div>
      </div>

      {/* Recent activity strip */}
      <div className="mt-3 flex items-center gap-2 text-xs">
        {lastRun ? (
          <>
            <RunStatusDot status={lastRun.status} />
            <span className="text-ink-700">
              Last run {formatDateTime(lastRun.started_at)}
            </span>
            {failedRecently && (
              <span className="text-red-700 ml-auto">⚠ recent failures</span>
            )}
          </>
        ) : (
          <span className="text-ink-400 italic">No runs yet</span>
        )}
      </div>

      {err && (
        <div className="mt-3 flex items-start gap-2 text-[11px] text-red-800 bg-red-50 border border-red-200 rounded p-2">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          <span>{err}</span>
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-1.5">
        <Button
          size="sm"
          onClick={() => call('run', () => api.post(`/workflows/${workflow.id}/run`))}
          disabled={busy !== null}
        >
          <Play size={14} /> {busy === 'run' ? 'Running…' : 'Run now'}
        </Button>

        {isActive ? (
          <Button
            size="sm"
            variant="outline"
            onClick={() => call('pause', () => api.post(`/workflows/${workflow.id}/pause`))}
            disabled={busy !== null}
          >
            <Pause size={14} /> Pause
          </Button>
        ) : (
          <Button
            size="sm"
            variant="outline"
            onClick={() => call('activate', () => api.post(`/workflows/${workflow.id}/activate`))}
            disabled={busy !== null}
          >
            <Play size={14} /> Activate
          </Button>
        )}

        <Button size="sm" variant="outline" onClick={onEdit} disabled={busy !== null}>
          <Edit3 size={14} /> Edit
        </Button>

        <Link href={`/workflows/${workflow.id}`}>
          <Button size="sm" variant="ghost" disabled={busy !== null}>
            <ExternalLink size={14} /> Details
          </Button>
        </Link>

        <Button
          size="sm" variant="ghost"
          onClick={() => call('duplicate', async () => {
            await api.post(`/workflows/${workflow.id}/duplicate`);
          })}
          disabled={busy !== null}
        >
          <Copy size={14} /> Duplicate
        </Button>

        <Button
          size="sm" variant="ghost"
          onClick={async () => {
            if (!confirm(`Delete "${workflow.name}"?`)) return;
            await call('delete', () => api.del(`/workflows/${workflow.id}`));
          }}
          disabled={busy !== null}
        >
          <Trash2 size={14} /> Delete
        </Button>
      </div>
    </Card>
  );
}

type RecentRun = {
  id: string;
  status: 'pending' | 'planning' | 'executing' | 'awaiting_review' | 'publishing' | 'succeeded' | 'failed' | 'cancelled' | 'needs_review';
  started_at: string;
  finished_at?: string | null;
};

function RunStatusDot({ status }: { status: RecentRun['status'] }) {
  if (status === 'succeeded') return <CheckCircle2 size={12} className="text-emerald-600" />;
  if (status === 'failed') return <XCircle size={12} className="text-red-600" />;
  if (status === 'awaiting_review' || status === 'needs_review') return <Clock size={12} className="text-amber-600" />;
  return <Clock size={12} className="text-ink-400" />;
}

function formatScheduleHint(w: Workflow): string {
  const s = w.schedule;
  switch (s.kind) {
    case 'manual':
      return 'Manual';
    case 'cron':
      return `Cron: ${s.cron || '—'}`;
    case 'interval':
      return `Every ${s.interval_minutes ?? '—'} min`;
    case 'once':
      return s.run_at ? `Once at ${new Date(s.run_at).toLocaleString()}` : 'Once';
    default:
      return s.kind;
  }
}

/* ───────── Edit dialog (unchanged from prior version) ───────────────── */

function EditWorkflowDialog({ workflow, onClose }: {
  workflow: Workflow;
  onClose: () => void;
}) {
  const { data: sources } = useApi<Source[]>('/sources');
  const { data: groups } = useApi<PlatformGroup[]>('/platforms/grouped');

  const [name, setName] = useState(workflow.name);
  const [description, setDescription] = useState(workflow.description ?? '');
  const [sourceIds, setSourceIds] = useState<string[]>(workflow.source_ids);
  const [platformIds, setPlatformIds] = useState<string[]>(workflow.platform_ids);
  const [scheduleKind, setScheduleKind] = useState(workflow.schedule.kind);
  const [cron, setCron] = useState(workflow.schedule.cron ?? '');
  const [intervalMinutes, setIntervalMinutes] = useState<string>(
    workflow.schedule.interval_minutes != null ? String(workflow.schedule.interval_minutes) : '',
  );
  const [runAt, setRunAt] = useState(
    workflow.schedule.run_at ? workflow.schedule.run_at.slice(0, 16) : '',
  );
  const [timezone, setTimezone] = useState(workflow.schedule.timezone);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function toggleSource(id: string) {
    setSourceIds(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id]);
  }
  function togglePlatform(id: string) {
    setPlatformIds(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id]);
  }

  async function save() {
    if (!name.trim()) {
      setErr("Name can't be empty.");
      return;
    }
    if (platformIds.length === 0) {
      setErr('Pick at least one target account so the workflow has somewhere to publish.');
      return;
    }
    setBusy(true); setErr(null);
    try {
      const body: Record<string, unknown> = {
        name,
        description,
        source_ids: sourceIds,
        platform_ids: platformIds,
        schedule: {
          kind: scheduleKind,
          cron: scheduleKind === 'cron' ? cron : null,
          interval_minutes: scheduleKind === 'interval' && intervalMinutes
            ? parseInt(intervalMinutes, 10) : null,
          run_at: scheduleKind === 'once' && runAt ? new Date(runAt).toISOString() : null,
          timezone,
        },
      };
      await api.patch(`/workflows/${workflow.id}`, body);
      await mutate('/workflows');
      onClose();
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Save failed.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50" onClick={onClose}>
      <Card
        className="w-full max-w-2xl max-h-[90vh] overflow-y-auto"
        onClick={(e: any) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-4">
          <div>
            <CardTitle>Edit workflow</CardTitle>
            <CardDescription>Change name, sources, accounts, and schedule.</CardDescription>
          </div>
          <button onClick={onClose} className="text-ink-500 hover:text-ink-700">
            <X size={18} />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-xs text-ink-500 mb-1">Name</label>
            <Input value={name} onChange={(e: any) => setName(e.target.value)} />
          </div>

          <div>
            <label className="block text-xs text-ink-500 mb-1">Description</label>
            <Textarea
              value={description}
              onChange={(e: any) => setDescription(e.target.value)}
              rows={2}
            />
          </div>

          <div>
            <label className="block text-xs text-ink-500 mb-1">
              Sources <span className="text-ink-400">({sourceIds.length} selected)</span>
            </label>
            {sources && sources.length === 0 ? (
              <p className="text-xs text-ink-500 italic">
                No sources yet — <Link href="/sources" className="underline">add one</Link>.
                Workflows can also run without sources (chat-driven).
              </p>
            ) : (
              <div className="space-y-1 max-h-32 overflow-y-auto rounded-lg border border-ink-200 p-2">
                {(sources ?? []).map(s => (
                  <label key={s.id} className="flex items-center gap-2 text-sm py-1 cursor-pointer hover:bg-ink-50 px-1 rounded">
                    <input
                      type="checkbox"
                      checked={sourceIds.includes(s.id)}
                      onChange={() => toggleSource(s.id)}
                    />
                    <span className="truncate">
                      {s.display_name} <span className="text-ink-500 text-xs">· {s.plugin_name}</span>
                    </span>
                  </label>
                ))}
              </div>
            )}
          </div>

          <div>
            <label className="block text-xs text-ink-500 mb-1">
              Target accounts <span className="text-ink-400">({platformIds.length} selected)</span>
              <span className="text-red-600 ml-1">*</span>
            </label>
            {groups && groups.length === 0 ? (
              <p className="text-xs text-ink-500 italic">
                No accounts connected — <Link href="/platforms" className="underline">connect one</Link>.
              </p>
            ) : (
              <div className="space-y-2 max-h-48 overflow-y-auto rounded-lg border border-ink-200 p-2">
                {(groups ?? []).map(g => (
                  <div key={g.plugin_name}>
                    <div className="text-xs font-semibold text-ink-700 mb-1">{g.display_name}</div>
                    {g.accounts.map(a => (
                      <label key={a.id} className="flex items-center gap-2 text-sm py-1 cursor-pointer hover:bg-ink-50 px-1 rounded">
                        <input
                          type="checkbox"
                          checked={platformIds.includes(a.id)}
                          onChange={() => togglePlatform(a.id)}
                        />
                        <span className="truncate flex items-center gap-1">
                          {a.display_name}
                          {a.account_handle && (
                            <span className="text-ink-500 text-xs"> · {a.account_handle}</span>
                          )}
                          <Badge tone={a.status === 'connected' ? 'success' : 'warning'}>
                            {a.status}
                          </Badge>
                        </span>
                      </label>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-ink-500 mb-1">Schedule</label>
              <select
                className="input"
                value={scheduleKind}
                onChange={(e) => setScheduleKind(e.target.value)}
              >
                {SCHEDULE_KINDS.map(k => (
                  <option key={k} value={k}>{k}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-ink-500 mb-1">Timezone</label>
              <Input value={timezone} onChange={(e: any) => setTimezone(e.target.value)} placeholder="UTC" />
            </div>

            {scheduleKind === 'cron' && (
              <div className="md:col-span-2">
                <label className="block text-xs text-ink-500 mb-1">
                  Cron expression
                  <span className="text-ink-400 ml-2">e.g. "0 9 * * *" = every day at 9am</span>
                </label>
                <Input
                  value={cron}
                  onChange={(e: any) => setCron(e.target.value)}
                  placeholder="0 9 * * *"
                  className="font-mono"
                />
              </div>
            )}
            {scheduleKind === 'interval' && (
              <div className="md:col-span-2">
                <label className="block text-xs text-ink-500 mb-1">Interval (minutes)</label>
                <Input
                  type="number"
                  value={intervalMinutes}
                  onChange={(e: any) => setIntervalMinutes(e.target.value)}
                  placeholder="60"
                />
              </div>
            )}
            {scheduleKind === 'once' && (
              <div className="md:col-span-2">
                <label className="block text-xs text-ink-500 mb-1">Run at</label>
                <Input
                  type="datetime-local"
                  value={runAt}
                  onChange={(e: any) => setRunAt(e.target.value)}
                />
              </div>
            )}
          </div>

          {err && (
            <div className="flex items-start gap-2 text-xs text-red-800 bg-red-50 border border-red-200 rounded p-2">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span>{err}</span>
            </div>
          )}
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={save} disabled={busy}>
            {busy ? 'Saving…' : 'Save changes'}
          </Button>
        </div>
      </Card>
    </div>
  );
}
