'use client';
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { Input } from '@/components/ui/Input';
import { useApi, api } from '@/lib/api/client';
import { ApiErrorBanner } from '@/components/ui/ApiErrorBanner';
import type { PluginInfo, Source } from '@/lib/api/types';
import { Database, Plus } from 'lucide-react';
import { useState } from 'react';
import { mutate } from 'swr';

export default function SourcesPage() {
  const { data: plugins, error: pluginsErr, mutate: retryPlugins } =
    useApi<PluginInfo[]>('/plugins?kind=source');
  const { data: sources, error: sourcesErr, mutate: retrySources } =
    useApi<Source[]>('/sources');
  const [chosen, setChosen] = useState<PluginInfo | null>(null);
  const [name, setName] = useState('');
  const [config, setConfig] = useState('{}');

  async function add() {
    if (!chosen) return;
    await api.post('/sources', {
      plugin_name: chosen.name,
      display_name: name || `${chosen.display_name} source`,
      config: JSON.parse(config),
    });
    setChosen(null); setName(''); setConfig('{}');
    await mutate('/sources');
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Sources" />
        <main className="flex-1 overflow-y-auto p-6 space-y-6">
          <ApiErrorBanner error={pluginsErr || sourcesErr}
                          retry={() => { retryPlugins(); retrySources(); }} />
          <section>
            <h2 className="text-sm font-semibold text-ink-700 mb-3">Configured sources</h2>
            {sources && sources.length === 0 ? (
              <EmptyState
                icon={<Database size={32} />}
                title="No sources yet"
                description="Add a content source — RSS, web page, or local folder — to feed the agents."
              />
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {(sources ?? []).map(s => (
                  <Card key={s.id}>
                    <div className="flex items-start justify-between">
                      <div>
                        <CardTitle>{s.display_name}</CardTitle>
                        <CardDescription>{s.plugin_name}</CardDescription>
                      </div>
                      <Badge tone={s.is_active ? 'success' : 'default'}>{s.is_active ? 'active' : 'paused'}</Badge>
                    </div>
                    <pre className="mt-3 text-xs bg-ink-50 p-2 rounded-lg max-h-24 overflow-auto">
                      {JSON.stringify(s.config, null, 2)}
                    </pre>
                  </Card>
                ))}
              </div>
            )}
          </section>

          <section>
            <h2 className="text-sm font-semibold text-ink-700 mb-3">Add a source</h2>
            <Card>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs text-ink-500 mb-1">Source plugin</label>
                  <select
                    className="input"
                    value={chosen?.name ?? ''}
                    onChange={(e) => setChosen((plugins ?? []).find(p => p.name === e.target.value) ?? null)}
                  >
                    <option value="">Select…</option>
                    {(plugins ?? []).map(p => (
                      <option key={p.name} value={p.name}>{p.display_name}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-ink-500 mb-1">Display name</label>
                  <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="My RSS feed" />
                </div>
                <div className="md:col-span-2">
                  <label className="block text-xs text-ink-500 mb-1">
                    Config (JSON — schema: {chosen ? <code className="font-mono text-[11px]">{JSON.stringify(chosen.config_schema)}</code> : '—'})
                  </label>
                  <textarea
                    className="input font-mono text-xs min-h-[120px]"
                    value={config}
                    onChange={(e) => setConfig(e.target.value)}
                  />
                </div>
                <div className="md:col-span-2 flex justify-end">
                  <Button onClick={add} disabled={!chosen}>
                    <Plus size={14} /> Create source
                  </Button>
                </div>
              </div>
            </Card>
          </section>
        </main>
      </div>
    </div>
  );
}
