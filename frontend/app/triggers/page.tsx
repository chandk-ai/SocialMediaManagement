'use client';
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { EmptyState } from '@/components/ui/EmptyState';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { useApi, api, ApiError } from '@/lib/api/client';
import type { PluginInfo, Trigger, Workflow } from '@/lib/api/types';
import {
  Zap, Plus, Webhook, MessageSquare, CalendarClock, MousePointerClick, Send,
  Sparkles, Edit3, Trash2, Pause, Play, AlertTriangle,
} from 'lucide-react';
import { useState, useEffect } from 'react';
import { mutate } from 'swr';
import { TelegramSetup } from '@/components/triggers/TelegramSetup';

const ICONS: Record<string, any> = {
  manual: MousePointerClick,
  schedule: CalendarClock,
  webhook: Webhook,
  whatsapp: MessageSquare,
  instagram: MessageSquare,
  telegram: Send,
};

// Promote Telegram first — it's the recommended chat trigger for this product
// (3-min @BotFather setup vs WhatsApp's days-long Meta verification). Manual
// and schedule come next so the basic loop is one click away. WhatsApp /
// Instagram drop to the bottom: real but heavier setup.
const PLUGIN_PRIORITY: Record<string, number> = {
  telegram: 0, manual: 1, schedule: 2, webhook: 3, slack: 4,
  whatsapp: 5, instagram: 6,
};
function sortPlugins(plugins: PluginInfo[]): PluginInfo[] {
  return [...plugins].sort((a, b) => {
    const ap = PLUGIN_PRIORITY[a.name] ?? 99;
    const bp = PLUGIN_PRIORITY[b.name] ?? 99;
    return ap - bp || a.display_name.localeCompare(b.display_name);
  });
}

export default function TriggersPage() {
  const { data: triggers } = useApi<Trigger[]>('/triggers');
  const { data: plugins } = useApi<PluginInfo[]>('/plugins?kind=trigger');
  const { data: reviewPlugins } = useApi<PluginInfo[]>('/plugins?kind=review_channel');
  const { data: workflows } = useApi<Workflow[]>('/workflows');
  const [adding, setAdding] = useState<PluginInfo | null>(null);
  const [addingTelegram, setAddingTelegram] = useState(false);
  // The trigger being edited. We pair it with its plugin metadata so
  // the edit dialog can show the same JSON-schema hint as create.
  const [editing, setEditing] = useState<Trigger | null>(null);
  const pluginByName = Object.fromEntries((plugins ?? []).map(p => [p.name, p]));

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Triggers" />
        <main className="flex-1 overflow-y-auto p-6 space-y-8">
          <section>
            <div className="mb-3">
              <h2 className="text-sm font-semibold text-ink-700">Configured triggers</h2>
              <p className="text-xs text-ink-500">
                A trigger ties a workflow to a way it can be started — manual, scheduled, webhook, or messaging (WhatsApp / Instagram).
              </p>
            </div>
            {triggers && triggers.length === 0 ? (
              <EmptyState
                icon={<Zap size={32} />}
                title="No triggers yet"
                description="Add a trigger below to let messages, schedules or webhooks start your workflows."
              />
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {(triggers ?? []).map(t => (
                  <TriggerCard
                    key={t.id}
                    trigger={t}
                    onEdit={() => setEditing(t)}
                  />
                ))}
              </div>
            )}
          </section>

          <section>
            <h2 className="text-sm font-semibold text-ink-700 mb-3">Add a trigger</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {sortPlugins(plugins ?? []).map(p => {
                const Icon = ICONS[p.name] ?? Zap;
                const isTelegram = p.name === 'telegram';
                return (
                  <Card key={p.name} className={isTelegram ? 'border-accent' : ''}>
                    <CardTitle className="flex items-center gap-2">
                      <Icon size={14} /> {p.display_name}
                      {isTelegram && (
                        <span className="ml-auto inline-flex items-center gap-1 text-[10px] uppercase tracking-wide text-accent font-medium">
                          <Sparkles size={10} /> recommended
                        </span>
                      )}
                    </CardTitle>
                    <CardDescription>{p.description}</CardDescription>
                    <div className="mt-4">
                      <Button
                        size="sm"
                        onClick={() => isTelegram ? setAddingTelegram(true) : setAdding(p)}
                      >
                        <Plus size={14} /> Configure
                      </Button>
                    </div>
                  </Card>
                );
              })}
            </div>
          </section>
        </main>

        {adding && (
          <AddTriggerDialog
            plugin={adding}
            workflows={workflows ?? []}
            reviewChannels={reviewPlugins ?? []}
            onClose={() => setAdding(null)}
            onCreated={async () => {
              setAdding(null);
              await mutate('/triggers');
            }}
          />
        )}
        {addingTelegram && (
          <TelegramSetup
            workflows={workflows ?? []}
            reviewChannelOptions={(reviewPlugins ?? []).map(r => ({
              name: r.name, display_name: r.display_name,
            }))}
            onClose={() => setAddingTelegram(false)}
            onCreated={async () => { await mutate('/triggers'); }}
          />
        )}
        {editing && (
          <EditTriggerDialog
            trigger={editing}
            plugin={pluginByName[editing.plugin_name] ?? null}
            workflows={workflows ?? []}
            reviewChannels={reviewPlugins ?? []}
            onClose={() => setEditing(null)}
            onSaved={async () => {
              setEditing(null);
              await mutate('/triggers');
            }}
          />
        )}
      </div>
    </div>
  );
}

/* ───────── Trigger card with Edit / Pause-Resume / Delete actions ──── */

function TriggerCard({
  trigger,
  onEdit,
}: {
  trigger: Trigger;
  onEdit: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const Icon = ICONS[trigger.kind] ?? Zap;
  // Trim long config previews so the card stays compact. Strip likely-
  // secret keys from the preview — the full unredacted config is only
  // shown in the edit dialog where the user is actively managing it.
  const safeConfigPreview = previewConfig(trigger.config);

  async function call(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    setErr(null);
    try {
      await fn();
      await mutate('/triggers');
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Action failed.');
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2 truncate">
            <Icon size={14} /> {trigger.display_name}
          </CardTitle>
          <CardDescription>
            {trigger.plugin_name}
            {trigger.review_channel ? ` · review via ${trigger.review_channel}` : ''}
          </CardDescription>
        </div>
        <Badge tone={trigger.is_active ? 'success' : 'default'}>
          {trigger.is_active ? 'active' : 'paused'}
        </Badge>
      </div>

      {trigger.allowed_senders.length > 0 && (
        <div className="mt-3 text-xs text-ink-500">
          allow: {trigger.allowed_senders.join(', ')}
        </div>
      )}

      {safeConfigPreview && (
        <pre className="mt-3 text-[11px] bg-ink-50 p-2 rounded-lg max-h-24 overflow-auto">
          {safeConfigPreview}
        </pre>
      )}

      {err && (
        <div className="mt-2 flex items-start gap-2 text-[11px] text-red-800 bg-red-50 border border-red-200 rounded p-2">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          <span>{err}</span>
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-1.5">
        <Button size="sm" variant="outline" onClick={onEdit} disabled={busy !== null}>
          <Edit3 size={12} /> Edit
        </Button>
        {trigger.is_active ? (
          <Button
            size="sm"
            variant="outline"
            disabled={busy !== null}
            onClick={() =>
              call('pause', () =>
                api.patch(`/triggers/${trigger.id}`, { is_active: false }),
              )
            }
          >
            <Pause size={12} /> {busy === 'pause' ? 'Pausing…' : 'Pause'}
          </Button>
        ) : (
          <Button
            size="sm"
            variant="outline"
            disabled={busy !== null}
            onClick={() =>
              call('resume', () =>
                api.patch(`/triggers/${trigger.id}`, { is_active: true }),
              )
            }
          >
            <Play size={12} /> {busy === 'resume' ? 'Resuming…' : 'Resume'}
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          disabled={busy !== null}
          onClick={() => {
            if (
              !confirm(
                `Delete trigger "${trigger.display_name}"? ` +
                `If it's a webhook, its URL stops working immediately. ` +
                `Use Pause for a reversible disable.`,
              )
            ) {
              return;
            }
            call('delete', () => api.del(`/triggers/${trigger.id}`));
          }}
        >
          <Trash2 size={12} /> Delete
        </Button>
      </div>
    </Card>
  );
}

/** Compact JSON preview that hides anything that looks secret. We don't
 *  pretend this is full redaction (the value still goes over the wire to
 *  /triggers); it just stops the card from displaying tokens at a glance. */
function previewConfig(cfg: Record<string, unknown>): string {
  const SECRET_HINT = /(token|secret|password|api_key|apikey)/i;
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(cfg ?? {})) {
    if (SECRET_HINT.test(k) && typeof v === 'string' && v) {
      out[k] = '••••' + (v.length > 4 ? v.slice(-4) : '');
    } else {
      out[k] = v;
    }
  }
  const json = JSON.stringify(out, null, 2);
  return json === '{}' ? '' : json;
}

/* ───────── Edit dialog (PATCH /triggers/{id}) ───────────────────────── */

function EditTriggerDialog({
  trigger, plugin, workflows, reviewChannels, onClose, onSaved,
}: {
  trigger: Trigger;
  plugin: PluginInfo | null;
  workflows: Workflow[];
  reviewChannels: PluginInfo[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  // Pre-populate every field from the current trigger.
  const [displayName, setDisplayName] = useState(trigger.display_name);
  const [config, setConfig] = useState(JSON.stringify(trigger.config, null, 2));
  const [allowed, setAllowed] = useState(trigger.allowed_senders.join(', '));
  const [reviewChannel, setReviewChannel] = useState(trigger.review_channel ?? '');
  const [reviewRecipient, setReviewRecipient] = useState(trigger.review_recipient ?? '');
  const [isActive, setIsActive] = useState(trigger.is_active);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Re-sync state if the user clicks Edit on a different trigger while
  // the dialog is still mounted (defensive — dialog usually unmounts).
  useEffect(() => {
    setDisplayName(trigger.display_name);
    setConfig(JSON.stringify(trigger.config, null, 2));
    setAllowed(trigger.allowed_senders.join(', '));
    setReviewChannel(trigger.review_channel ?? '');
    setReviewRecipient(trigger.review_recipient ?? '');
    setIsActive(trigger.is_active);
  }, [trigger.id, trigger.display_name, trigger.config,
      trigger.allowed_senders, trigger.review_channel,
      trigger.review_recipient, trigger.is_active]);

  async function submit() {
    setSaving(true);
    setErr(null);
    // Validate JSON locally so the user sees the error in the dialog
    // rather than as a 422 toast.
    let parsedConfig: Record<string, unknown>;
    try {
      parsedConfig = JSON.parse(config || '{}');
    } catch (e) {
      setErr(`Config is not valid JSON: ${(e as Error).message}`);
      setSaving(false);
      return;
    }
    // Send the full intended state — backend stores partial-update
    // semantics (any field omitted is preserved), but we always send
    // everything so a careless tweak to one field doesn't accidentally
    // freeze old values.
    try {
      await api.patch(`/triggers/${trigger.id}`, {
        display_name: displayName,
        config: parsedConfig,
        allowed_senders: allowed.split(',').map(s => s.trim()).filter(Boolean),
        review_channel: reviewChannel || null,
        review_recipient: reviewRecipient || null,
        is_active: isActive,
      });
      await onSaved();
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Save failed.');
      setSaving(false);
    }
  }

  // Workflow binding is read-only in edit mode — see TriggerUpdate
  // docstring for why (delete + recreate if you need to rebind).
  const workflow = workflows.find(w => w.id === trigger.workflow_id);

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardTitle>Edit {trigger.display_name}</CardTitle>
        <CardDescription>
          {trigger.plugin_name} trigger on workflow{' '}
          <span className="font-medium">{workflow?.name ?? '(unknown)'}</span>.
          To change the plugin or workflow binding, delete and recreate.
        </CardDescription>

        <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-ink-500 mb-1">Display name</label>
            <Input value={displayName} onChange={e => setDisplayName(e.target.value)} />
          </div>
          <div>
            <label className="block text-xs text-ink-500 mb-1">Status</label>
            <label className="flex items-center gap-2 mt-1.5 text-sm">
              <input
                type="checkbox"
                className="h-4 w-4 accent-accent"
                checked={isActive}
                onChange={e => setIsActive(e.target.checked)}
              />
              <span>{isActive ? 'Active — will fire' : 'Paused — webhooks ACK but don\'t dispatch'}</span>
            </label>
          </div>

          <div className="md:col-span-2">
            <label className="block text-xs text-ink-500 mb-1">
              Config (JSON)
              {plugin?.config_schema ? (
                <>
                  {' · schema: '}
                  <code className="font-mono text-[11px]">
                    {JSON.stringify(plugin.config_schema)}
                  </code>
                </>
              ) : null}
            </label>
            <textarea
              className="input font-mono text-xs min-h-[140px]"
              value={config}
              onChange={e => setConfig(e.target.value)}
            />
            <p className="text-[11px] text-ink-500 mt-1">
              Editing config rotates any secrets in it immediately — the next event uses the new values.
            </p>
          </div>

          <div className="md:col-span-2">
            <label className="block text-xs text-ink-500 mb-1">
              Allowed senders (comma-separated phone numbers / handles / emails) — empty = anyone
            </label>
            <Input
              value={allowed}
              onChange={e => setAllowed(e.target.value)}
              placeholder="+15551234567, @founder"
            />
          </div>
          <div>
            <label className="block text-xs text-ink-500 mb-1">Review channel</label>
            <select
              className="input"
              value={reviewChannel}
              onChange={e => setReviewChannel(e.target.value)}
            >
              <option value="">(none)</option>
              {reviewChannels.map(r => (
                <option key={r.name} value={r.name}>{r.display_name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-ink-500 mb-1">
              Review recipient (phone / IG id / email / channel)
            </label>
            <Input
              value={reviewRecipient}
              onChange={e => setReviewRecipient(e.target.value)}
              placeholder="+15551234567"
            />
          </div>
        </div>

        {err && (
          <div className="mt-3 flex items-start gap-2 text-xs text-red-800 bg-red-50 border border-red-200 rounded p-2">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            <span>{err}</span>
          </div>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button onClick={submit} disabled={saving || !displayName}>
            {saving ? 'Saving…' : 'Save changes'}
          </Button>
        </div>
      </Card>
    </div>
  );
}

function AddTriggerDialog({
  plugin, workflows, reviewChannels, onClose, onCreated,
}: {
  plugin: PluginInfo;
  workflows: Workflow[];
  reviewChannels: PluginInfo[];
  onClose: () => void;
  onCreated: () => Promise<void>;
}) {
  const [workflowId, setWorkflowId] = useState('');
  const [displayName, setDisplayName] = useState(`${plugin.display_name} trigger`);
  const [config, setConfig] = useState('{}');
  const [allowed, setAllowed] = useState('');
  const [reviewChannel, setReviewChannel] = useState('in_app');
  const [reviewRecipient, setReviewRecipient] = useState('');

  async function submit() {
    await api.post('/triggers', {
      workflow_id: workflowId,
      plugin_name: plugin.name,
      display_name: displayName,
      config: JSON.parse(config || '{}'),
      allowed_senders: allowed.split(',').map(s => s.trim()).filter(Boolean),
      review_channel: reviewChannel || null,
      review_recipient: reviewRecipient || null,
    });
    await onCreated();
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50">
      <Card className="w-full max-w-2xl">
        <CardTitle>Configure {plugin.display_name} trigger</CardTitle>
        <CardDescription>
          Bind to a workflow + decide where drafts get reviewed.
        </CardDescription>
        <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-ink-500 mb-1">Workflow</label>
            <select className="input" value={workflowId} onChange={e => setWorkflowId(e.target.value)}>
              <option value="">Select…</option>
              {workflows.map(w => (
                <option key={w.id} value={w.id}>{w.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-ink-500 mb-1">Display name</label>
            <Input value={displayName} onChange={e => setDisplayName(e.target.value)} />
          </div>
          <div className="md:col-span-2">
            <label className="block text-xs text-ink-500 mb-1">
              Config (JSON; schema:&nbsp;
              <code className="font-mono text-[11px]">
                {JSON.stringify(plugin.config_schema)}
              </code>)
            </label>
            <textarea className="input font-mono text-xs min-h-[110px]"
                      value={config} onChange={e => setConfig(e.target.value)} />
          </div>
          <div className="md:col-span-2">
            <label className="block text-xs text-ink-500 mb-1">
              Allowed senders (comma-separated phone numbers / handles / emails) — empty = anyone
            </label>
            <Input value={allowed} onChange={e => setAllowed(e.target.value)}
                   placeholder="+15551234567, @founder" />
          </div>
          <div>
            <label className="block text-xs text-ink-500 mb-1">Review channel</label>
            <select className="input" value={reviewChannel} onChange={e => setReviewChannel(e.target.value)}>
              {reviewChannels.map(r => (
                <option key={r.name} value={r.name}>{r.display_name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-ink-500 mb-1">
              Review recipient (phone / IG id / email / channel)
            </label>
            <Input value={reviewRecipient} onChange={e => setReviewRecipient(e.target.value)}
                   placeholder="+15551234567" />
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={!workflowId}>Create trigger</Button>
        </div>
      </Card>
    </div>
  );
}
