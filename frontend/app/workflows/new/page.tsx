'use client';
/**
 * Multi-step "New workflow" wizard. Guides users from blank → fully-configured
 * workflow with inline validation and contextual help so nobody wonders
 * "what do I do next".
 *
 * Steps:
 *   1. Choose a template (or start blank)
 *   2. Sources (with inline "no sources yet" CTA)
 *   3. Target accounts (with inline "no accounts yet" CTA)
 *   4. Voice & schedule
 *   5. Review & create
 */
import { Suspense, useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { mutate } from 'swr';

import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Input, Textarea } from '@/components/ui/Input';
import { Badge } from '@/components/ui/Badge';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { useApi, api, ApiError } from '@/lib/api/client';
import type { PlatformGroup, Source, PluginInfo } from '@/lib/api/types';
import {
  WORKFLOW_TEMPLATES, findTemplate, type WorkflowTemplate,
} from '@/lib/workflowTemplates';
import {
  ArrowLeft, ArrowRight, Check, Sparkles, Database, Plug,
  AlertTriangle, ExternalLink, Newspaper, Calendar, MessageSquare,
  FolderOpen,
} from 'lucide-react';

export const dynamic = 'force-dynamic';

const ICONS = { Newspaper, Calendar, MessageSquare, FolderOpen, Sparkles } as const;

const STEPS = [
  { key: 'template', label: 'Template' },
  { key: 'sources', label: 'Sources' },
  { key: 'platforms', label: 'Accounts' },
  { key: 'voice', label: 'Voice & schedule' },
  { key: 'review', label: 'Review' },
] as const;

type StepKey = typeof STEPS[number]['key'];

export default function NewWorkflowPage() {
  return (
    <Suspense fallback={<div />}>
      <Wizard />
    </Suspense>
  );
}

function Wizard() {
  const router = useRouter();
  const params = useSearchParams();
  const initialTemplate = params.get('template');

  const { data: sources }   = useApi<Source[]>('/sources');
  const { data: groups }    = useApi<PlatformGroup[]>('/platforms/grouped');
  const { data: llms }      = useApi<PluginInfo[]>('/plugins?kind=llm');
  const { data: llmKeys }   = useApi<{
    preferred_provider: string | null;
    preferred_model: string | null;
    providers: Array<{ provider: string; is_set: boolean }>;
  }>('/llm-keys');

  const [step, setStep] = useState<StepKey>(initialTemplate ? 'sources' : 'template');
  const [template, setTemplate] = useState<WorkflowTemplate | null>(
    initialTemplate ? findTemplate(initialTemplate) ?? null : null,
  );

  // Form state — seeded from template defaults when chosen.
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [pickedSources, setPickedSources] = useState<string[]>([]);
  const [pickedPlatforms, setPickedPlatforms] = useState<string[]>([]);
  const [tone, setTone] = useState('professional');
  const [audience, setAudience] = useState('general');
  const [requireApproval, setRequireApproval] = useState(true);
  const [llm, setLlm] = useState('mock');
  const [scheduleKind, setScheduleKind] = useState<'manual' | 'cron' | 'interval' | 'once'>('manual');
  const [cron, setCron] = useState('');
  const [intervalMinutes, setIntervalMinutes] = useState('');
  const [runAt, setRunAt] = useState('');
  const [timezone, setTimezone] = useState('UTC');

  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Apply template defaults when one is chosen.
  useEffect(() => {
    if (!template) return;
    const d = template.defaults;
    setName(d.name);
    setDescription(d.description);
    setTone(d.config.tone);
    setAudience(d.config.audience);
    setRequireApproval(d.config.require_human_approval);
    setScheduleKind(d.schedule.kind);
    setCron(d.schedule.cron ?? '');
    setIntervalMinutes(d.schedule.interval_minutes != null ? String(d.schedule.interval_minutes) : '');
    setTimezone(d.schedule.timezone);
  }, [template]);

  // Default the LLM provider to the org's preferred one (set in Settings).
  useEffect(() => {
    if (llmKeys?.preferred_provider) {
      setLlm(llmKeys.preferred_provider);
    }
  }, [llmKeys?.preferred_provider]);

  function toggle(arr: string[], setArr: (v: string[]) => void, id: string) {
    setArr(arr.includes(id) ? arr.filter(x => x !== id) : [...arr, id]);
  }

  // Filter source / platform suggestions to those the template recommends, but
  // never block the user from picking others.
  const suggestedSourceIds = useMemo(() => {
    if (!template) return new Set<string>();
    return new Set(
      (sources ?? [])
        .filter(s => template.recommendedSources.includes(s.plugin_name))
        .map(s => s.id),
    );
  }, [template, sources]);
  const suggestedPlatformIds = useMemo(() => {
    if (!template) return new Set<string>();
    const ids = new Set<string>();
    (groups ?? []).forEach(g => {
      if (template.recommendedPlatforms.includes(g.plugin_name)) {
        g.accounts.forEach(a => ids.add(a.id));
      }
    });
    return ids;
  }, [template, groups]);

  // Validation per step
  const stepIndex = STEPS.findIndex(s => s.key === step);
  const canAdvance = (() => {
    switch (step) {
      case 'template': return true;            // optional
      case 'sources':  return true;            // sources are optional (chat-driven workflows are valid)
      case 'platforms': return pickedPlatforms.length > 0;
      case 'voice':    return scheduleKind !== 'cron' || !!cron;
      case 'review':   return !!name.trim();
      default: return true;
    }
  })();
  function next() {
    if (!canAdvance) return;
    const i = STEPS.findIndex(s => s.key === step);
    if (i < STEPS.length - 1) setStep(STEPS[i + 1].key);
  }
  function prev() {
    const i = STEPS.findIndex(s => s.key === step);
    if (i > 0) setStep(STEPS[i - 1].key);
  }

  async function submit() {
    if (!name.trim()) {
      setErr('Pick a name for the workflow.');
      setStep('review');
      return;
    }
    if (pickedPlatforms.length === 0) {
      setErr('Select at least one target account.');
      setStep('platforms');
      return;
    }
    setBusy(true); setErr(null);
    try {
      await api.post('/workflows', {
        name,
        description,
        source_ids: pickedSources,
        platform_ids: pickedPlatforms,
        config: {
          tone, audience,
          require_human_approval: requireApproval,
          llm_provider: llm,
        },
        schedule: {
          kind: scheduleKind,
          cron: scheduleKind === 'cron' ? cron : null,
          interval_minutes: scheduleKind === 'interval' && intervalMinutes
            ? parseInt(intervalMinutes, 10) : null,
          run_at: scheduleKind === 'once' && runAt ? new Date(runAt).toISOString() : null,
          timezone,
        },
      });
      await mutate('/workflows');
      router.push('/workflows');
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Failed to create workflow.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="New workflow" />
        <main className="flex-1 overflow-y-auto p-6">
          <Link href="/workflows" className="inline-flex items-center gap-1 text-sm text-ink-500 hover:text-ink-700 mb-4">
            <ArrowLeft size={14} /> Back to workflows
          </Link>

          {/* Stepper */}
          <Stepper current={step} />

          <div className="mt-6 max-w-3xl mx-auto">
            {step === 'template' && (
              <StepTemplate
                template={template}
                onPick={(t) => { setTemplate(t); next(); }}
                onSkip={() => next()}
              />
            )}
            {step === 'sources' && (
              <StepSources
                sources={sources ?? []}
                picked={pickedSources}
                togglePicked={(id) => toggle(pickedSources, setPickedSources, id)}
                suggested={suggestedSourceIds}
                template={template}
              />
            )}
            {step === 'platforms' && (
              <StepPlatforms
                groups={groups ?? []}
                picked={pickedPlatforms}
                togglePicked={(id) => toggle(pickedPlatforms, setPickedPlatforms, id)}
                suggested={suggestedPlatformIds}
                template={template}
              />
            )}
            {step === 'voice' && (
              <StepVoice
                name={name} setName={setName}
                description={description} setDescription={setDescription}
                tone={tone} setTone={setTone}
                audience={audience} setAudience={setAudience}
                requireApproval={requireApproval} setRequireApproval={setRequireApproval}
                llm={llm} setLlm={setLlm} llms={llms ?? []}
                scheduleKind={scheduleKind} setScheduleKind={setScheduleKind}
                cron={cron} setCron={setCron}
                intervalMinutes={intervalMinutes} setIntervalMinutes={setIntervalMinutes}
                runAt={runAt} setRunAt={setRunAt}
                timezone={timezone} setTimezone={setTimezone}
              />
            )}
            {step === 'review' && (
              <StepReview
                name={name} description={description}
                pickedSources={pickedSources} sources={sources ?? []}
                pickedPlatforms={pickedPlatforms} groups={groups ?? []}
                tone={tone} audience={audience}
                requireApproval={requireApproval}
                scheduleKind={scheduleKind} cron={cron}
                intervalMinutes={intervalMinutes} runAt={runAt} timezone={timezone}
              />
            )}

            {err && (
              <div className="mt-4 flex items-start gap-2 text-xs text-red-800 bg-red-50 border border-red-200 rounded p-2">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                <span>{err}</span>
              </div>
            )}

            {/* Nav buttons */}
            <div className="mt-6 flex justify-between items-center">
              <Button variant="ghost" onClick={prev} disabled={stepIndex === 0 || busy}>
                <ArrowLeft size={14} /> Back
              </Button>
              {step === 'review' ? (
                <Button onClick={submit} disabled={!name.trim() || pickedPlatforms.length === 0 || busy}>
                  <Check size={14} /> {busy ? 'Creating…' : 'Create workflow'}
                </Button>
              ) : (
                <Button onClick={next} disabled={!canAdvance}>
                  Next <ArrowRight size={14} />
                </Button>
              )}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}

/* ───────── Stepper ──────────────────────────────────────────────────── */

function Stepper({ current }: { current: StepKey }) {
  const idx = STEPS.findIndex(s => s.key === current);
  return (
    <div className="flex items-center gap-2 max-w-3xl mx-auto">
      {STEPS.map((s, i) => (
        <div key={s.key} className="flex items-center gap-2 flex-1">
          <div
            className={`h-7 w-7 rounded-full flex items-center justify-center text-xs font-medium ${
              i < idx ? 'bg-accent text-white' :
              i === idx ? 'bg-accent-muted text-accent ring-2 ring-accent' :
              'bg-ink-100 text-ink-500'
            }`}
          >
            {i < idx ? <Check size={14} /> : i + 1}
          </div>
          <span className={`text-xs ${i === idx ? 'font-medium text-ink-900' : 'text-ink-500'}`}>
            {s.label}
          </span>
          {i < STEPS.length - 1 && (
            <div className={`flex-1 h-px ${i < idx ? 'bg-accent' : 'bg-ink-200'}`} />
          )}
        </div>
      ))}
    </div>
  );
}

/* ───────── Step 1: Template ─────────────────────────────────────────── */

function StepTemplate({ template, onPick, onSkip }: {
  template: WorkflowTemplate | null;
  onPick: (t: WorkflowTemplate) => void;
  onSkip: () => void;
}) {
  return (
    <Card>
      <CardTitle>Start from a template</CardTitle>
      <CardDescription>
        Templates fill in the schedule, tone, and platform mapping for you.
        You can change any of it on later steps.
      </CardDescription>
      <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3">
        {WORKFLOW_TEMPLATES.map((t) => {
          const Icon = ICONS[t.icon];
          const active = template?.key === t.key;
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => onPick(t)}
              className={`text-left rounded-xl border p-3 transition-colors ${
                active ? 'border-accent bg-accent-muted/30' : 'border-ink-200 hover:border-accent'
              }`}
            >
              <div className="flex items-start gap-3">
                <div className="rounded-lg bg-accent-muted p-2 text-accent shrink-0">
                  <Icon size={18} />
                </div>
                <div className="min-w-0">
                  <div className="text-sm font-medium text-ink-900">{t.title}</div>
                  <div className="text-xs text-ink-500 mt-0.5">{t.tagline}</div>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {t.recommendedPlatforms.map((p) => (
                      <Badge key={p} tone="default">{p}</Badge>
                    ))}
                  </div>
                </div>
              </div>
            </button>
          );
        })}
      </div>
      <div className="mt-4 text-center">
        <Button variant="ghost" size="sm" onClick={onSkip}>
          Skip — start blank instead
        </Button>
      </div>
    </Card>
  );
}

/* ───────── Step 2: Sources ──────────────────────────────────────────── */

function StepSources({ sources, picked, togglePicked, suggested, template }: {
  sources: Source[];
  picked: string[];
  togglePicked: (id: string) => void;
  suggested: Set<string>;
  template: WorkflowTemplate | null;
}) {
  const recommended = (template?.recommendedSources ?? []);
  const hasRecommended = recommended.length > 0 &&
    sources.some(s => recommended.includes(s.plugin_name));
  return (
    <Card>
      <CardTitle>Where should the agents pull content from?</CardTitle>
      <CardDescription>
        {recommended.length > 0
          ? `This template suggests: ${recommended.join(', ')}.`
          : 'Sources are optional. Skip to use chat-driven workflows (you type the directive).'}
      </CardDescription>

      {sources.length === 0 ? (
        <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm">
          <div className="flex items-start gap-2">
            <Database size={18} className="text-amber-700 shrink-0 mt-0.5" />
            <div className="flex-1">
              <div className="font-medium text-amber-900">No sources yet</div>
              <p className="text-xs text-amber-800/80 mt-0.5">
                {recommended.length > 0
                  ? `Add a ${recommended[0]} source first, then come back to this wizard.`
                  : 'Add at least one source so the agents have material to work from.'}
              </p>
              <Link href="/sources" className="inline-block mt-2">
                <Button size="sm" variant="outline">
                  <Database size={12} /> Open Sources <ExternalLink size={12} />
                </Button>
              </Link>
              <p className="text-[11px] text-amber-700 mt-2">
                Or skip — chat-driven workflows can run without sources.
              </p>
            </div>
          </div>
        </div>
      ) : (
        <div className="mt-4 space-y-1 max-h-64 overflow-y-auto rounded-lg border border-ink-200 p-2">
          {sources.map(s => (
            <label key={s.id} className="flex items-center gap-2 text-sm py-1 cursor-pointer hover:bg-ink-50 px-2 rounded">
              <input type="checkbox" checked={picked.includes(s.id)} onChange={() => togglePicked(s.id)} />
              <span className="truncate flex-1">
                {s.display_name}{' '}
                <span className="text-ink-500 text-xs">· {s.plugin_name}</span>
              </span>
              {suggested.has(s.id) && (
                <Badge tone="success">recommended</Badge>
              )}
            </label>
          ))}
        </div>
      )}

      {recommended.length > 0 && !hasRecommended && sources.length > 0 && (
        <div className="mt-3 text-[11px] text-amber-700">
          ⚠ This template recommends a {recommended.join(' / ')} source — none configured yet.
          You can still proceed but results may be limited.
        </div>
      )}
    </Card>
  );
}

/* ───────── Step 3: Platforms ────────────────────────────────────────── */

function StepPlatforms({ groups, picked, togglePicked, suggested, template }: {
  groups: PlatformGroup[];
  picked: string[];
  togglePicked: (id: string) => void;
  suggested: Set<string>;
  template: WorkflowTemplate | null;
}) {
  const recommended = (template?.recommendedPlatforms ?? []);
  return (
    <Card>
      <CardTitle>Which accounts should publish?</CardTitle>
      <CardDescription>
        {recommended.length > 0
          ? `This template suggests: ${recommended.join(', ')}.`
          : 'Pick at least one connected account so the workflow has somewhere to publish.'}
      </CardDescription>

      {groups.length === 0 ? (
        <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm">
          <div className="flex items-start gap-2">
            <Plug size={18} className="text-amber-700 shrink-0 mt-0.5" />
            <div className="flex-1">
              <div className="font-medium text-amber-900">No accounts connected yet</div>
              <p className="text-xs text-amber-800/80 mt-0.5">
                Workflows need at least one account so they have somewhere to publish.
                {recommended.length > 0 && (
                  <> This template recommends connecting a {recommended.join(' or ')} account.</>
                )}
              </p>
              <Link href="/platforms" className="inline-block mt-2">
                <Button size="sm" variant="outline">
                  <Plug size={12} /> Open Platforms <ExternalLink size={12} />
                </Button>
              </Link>
            </div>
          </div>
        </div>
      ) : (
        <div className="mt-4 space-y-3 max-h-72 overflow-y-auto rounded-lg border border-ink-200 p-2">
          {groups.map(g => (
            <div key={g.plugin_name}>
              <div className="text-xs font-semibold text-ink-700 mb-1">{g.display_name}</div>
              {g.accounts.map(a => (
                <label key={a.id} className="flex items-center gap-2 text-sm py-1 cursor-pointer hover:bg-ink-50 px-2 rounded">
                  <input type="checkbox" checked={picked.includes(a.id)} onChange={() => togglePicked(a.id)} />
                  <span className="truncate flex-1">
                    {a.display_name}
                    {a.account_handle && (
                      <span className="text-ink-500 text-xs"> · {a.account_handle}</span>
                    )}
                  </span>
                  <Badge tone={a.status === 'connected' ? 'success' : 'warning'}>
                    {a.status}
                  </Badge>
                  {suggested.has(a.id) && <Badge tone="success">recommended</Badge>}
                </label>
              ))}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

/* ───────── Step 4: Voice & schedule ─────────────────────────────────── */

function StepVoice(props: any) {
  const {
    name, setName, description, setDescription,
    tone, setTone, audience, setAudience,
    requireApproval, setRequireApproval,
    llm, setLlm, llms,
    scheduleKind, setScheduleKind,
    cron, setCron,
    intervalMinutes, setIntervalMinutes,
    runAt, setRunAt,
    timezone, setTimezone,
  } = props;
  return (
    <Card>
      <CardTitle>Voice & schedule</CardTitle>
      <CardDescription>How the drafts should sound and when they should run.</CardDescription>

      <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="md:col-span-2">
          <label className="block text-xs text-ink-500 mb-1">
            Name <span className="text-red-600">*</span>
          </label>
          <Input value={name} onChange={(e: any) => setName(e.target.value)} placeholder="e.g. Daily IG + LinkedIn from blog RSS" />
        </div>
        <div className="md:col-span-2">
          <label className="block text-xs text-ink-500 mb-1">Description</label>
          <Textarea value={description} onChange={(e: any) => setDescription(e.target.value)} rows={2} />
        </div>
        <div>
          <label className="block text-xs text-ink-500 mb-1">Tone</label>
          <Input value={tone} onChange={(e: any) => setTone(e.target.value)} placeholder="professional, conversational, witty…" />
        </div>
        <div>
          <label className="block text-xs text-ink-500 mb-1">Audience</label>
          <Input value={audience} onChange={(e: any) => setAudience(e.target.value)} placeholder="non-profit donors, dev community…" />
        </div>
        <div>
          <label className="block text-xs text-ink-500 mb-1">LLM provider</label>
          <select className="input" value={llm} onChange={(e: any) => setLlm(e.target.value)}>
            {(llms ?? []).map((l: PluginInfo) => (
              <option key={l.name} value={l.name}>{l.display_name}</option>
            ))}
          </select>
        </div>
        <div className="flex items-end">
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={requireApproval}
              onChange={(e) => setRequireApproval(e.target.checked)}
            />
            Require human approval before publishing
          </label>
        </div>

        <div className="md:col-span-2 border-t border-ink-100 pt-4 mt-2">
          <h3 className="text-xs font-semibold text-ink-700 mb-3">Schedule</h3>
        </div>
        <div>
          <label className="block text-xs text-ink-500 mb-1">When does it run?</label>
          <select className="input" value={scheduleKind} onChange={(e: any) => setScheduleKind(e.target.value)}>
            <option value="manual">Manual (run on demand)</option>
            <option value="cron">Cron expression</option>
            <option value="interval">Every N minutes</option>
            <option value="once">Once at a specific time</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-ink-500 mb-1">Timezone</label>
          <Input value={timezone} onChange={(e: any) => setTimezone(e.target.value)} placeholder="UTC" />
        </div>

        {scheduleKind === 'cron' && (
          <div className="md:col-span-2">
            <label className="block text-xs text-ink-500 mb-1">
              Cron expression <span className="text-ink-400">e.g. "0 9 * * *" = every day 9am</span>
            </label>
            <Input value={cron} onChange={(e: any) => setCron(e.target.value)} className="font-mono" placeholder="0 9 * * *" />
          </div>
        )}
        {scheduleKind === 'interval' && (
          <div className="md:col-span-2">
            <label className="block text-xs text-ink-500 mb-1">Interval (minutes)</label>
            <Input type="number" value={intervalMinutes} onChange={(e: any) => setIntervalMinutes(e.target.value)} placeholder="60" />
          </div>
        )}
        {scheduleKind === 'once' && (
          <div className="md:col-span-2">
            <label className="block text-xs text-ink-500 mb-1">Run at</label>
            <Input type="datetime-local" value={runAt} onChange={(e: any) => setRunAt(e.target.value)} />
          </div>
        )}
      </div>
    </Card>
  );
}

/* ───────── Step 5: Review ───────────────────────────────────────────── */

function StepReview(props: any) {
  const {
    name, description,
    pickedSources, sources,
    pickedPlatforms, groups,
    tone, audience, requireApproval,
    scheduleKind, cron, intervalMinutes, runAt, timezone,
  } = props;
  const sourceLabels = (sources as Source[])
    .filter((s: Source) => pickedSources.includes(s.id))
    .map((s: Source) => s.display_name);
  const platformLabels = (groups as PlatformGroup[])
    .flatMap((g: PlatformGroup) => g.accounts)
    .filter((a: any) => pickedPlatforms.includes(a.id))
    .map((a: any) => `${a.display_name} (${a.plugin_name})`);

  return (
    <Card>
      <CardTitle>Review & create</CardTitle>
      <CardDescription>One last look before the workflow is created.</CardDescription>
      <dl className="mt-4 grid grid-cols-1 md:grid-cols-[140px_1fr] gap-y-3 text-sm">
        <dt className="text-ink-500">Name</dt>
        <dd className="font-medium">{name || <span className="text-red-600">— required</span>}</dd>
        <dt className="text-ink-500">Description</dt>
        <dd>{description || <em className="text-ink-400">none</em>}</dd>
        <dt className="text-ink-500">Sources</dt>
        <dd>
          {sourceLabels.length > 0
            ? sourceLabels.join(', ')
            : <em className="text-ink-400">none — chat-driven</em>}
        </dd>
        <dt className="text-ink-500">Target accounts</dt>
        <dd>
          {platformLabels.length > 0
            ? platformLabels.join(', ')
            : <span className="text-red-600">— at least one required</span>}
        </dd>
        <dt className="text-ink-500">Voice</dt>
        <dd>{tone}{audience ? ` for ${audience}` : ''}</dd>
        <dt className="text-ink-500">Approval</dt>
        <dd>{requireApproval ? 'Human approves before publishing' : 'Auto-publish on approve'}</dd>
        <dt className="text-ink-500">Schedule</dt>
        <dd>
          {scheduleKind === 'manual' && 'Manual'}
          {scheduleKind === 'cron' && <>cron <code className="font-mono text-xs">{cron}</code> ({timezone})</>}
          {scheduleKind === 'interval' && <>every {intervalMinutes || '—'} min</>}
          {scheduleKind === 'once' && <>once at {runAt} ({timezone})</>}
        </dd>
      </dl>
    </Card>
  );
}
