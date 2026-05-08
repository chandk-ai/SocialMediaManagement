'use client';
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { EmptyState } from '@/components/ui/EmptyState';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { useApi, api } from '@/lib/api/client';
import type { PluginInfo, Trigger, Workflow } from '@/lib/api/types';
import { Zap, Plus, Webhook, MessageSquare, CalendarClock, MousePointerClick, Send, Sparkles } from 'lucide-react';
import { useState } from 'react';
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
                {(triggers ?? []).map(t => {
                  const Icon = ICONS[t.kind] ?? Zap;
                  return (
                    <Card key={t.id}>
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <CardTitle className="flex items-center gap-2 truncate">
                            <Icon size={14} /> {t.display_name}
                          </CardTitle>
                          <CardDescription>
                            {t.plugin_name}
                            {t.review_channel ? ` · review via ${t.review_channel}` : ''}
                          </CardDescription>
                        </div>
                        <Badge tone={t.is_active ? 'success' : 'default'}>
                          {t.is_active ? 'active' : 'paused'}
                        </Badge>
                      </div>
                      {t.allowed_senders.length > 0 && (
                        <div className="mt-3 text-xs text-ink-500">
                          allow: {t.allowed_senders.join(', ')}
                        </div>
                      )}
                      <pre className="mt-3 text-[11px] bg-ink-50 p-2 rounded-lg max-h-24 overflow-auto">
                        {JSON.stringify(t.config, null, 2)}
                      </pre>
                    </Card>
                  );
                })}
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
      </div>
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
