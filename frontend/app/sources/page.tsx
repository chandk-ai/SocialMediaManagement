'use client';
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { Input } from '@/components/ui/Input';
import { JsonSchemaForm } from '@/components/ui/JsonSchemaForm';
import { useApi, api, ApiError } from '@/lib/api/client';
import { ApiErrorBanner } from '@/components/ui/ApiErrorBanner';
import type { PluginInfo, Source } from '@/lib/api/types';
import { Database, Plus, AlertTriangle } from 'lucide-react';
import { useState } from 'react';
import { mutate } from 'swr';

export default function SourcesPage() {
  const { data: plugins, error: pluginsErr, mutate: retryPlugins } =
    useApi<PluginInfo[]>('/plugins?kind=source');
  const { data: sources, error: sourcesErr, mutate: retrySources } =
    useApi<Source[]>('/sources');
  const [chosen, setChosen] = useState<PluginInfo | null>(null);
  const [name, setName] = useState('');
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [submitErr, setSubmitErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function pickPlugin(p: PluginInfo | null) {
    setChosen(p);
    setConfig({});                  // reset config when switching plugin
    setSubmitErr(null);
  }

  function missingRequired(): string[] {
    const required = (chosen?.config_schema as any)?.required ?? [];
    return required.filter((k: string) => {
      const v = (config as any)[k];
      return v === undefined || v === '' || (Array.isArray(v) && v.length === 0);
    });
  }

  async function add() {
    if (!chosen) return;
    const missing = missingRequired();
    if (missing.length) {
      setSubmitErr(`Missing required fields: ${missing.join(', ')}`);
      return;
    }
    setBusy(true); setSubmitErr(null);
    try {
      await api.post('/sources', {
        plugin_name: chosen.name,
        display_name: name || `${chosen.display_name} source`,
        config,
      });
      pickPlugin(null);
      setName('');
      await mutate('/sources');
    } catch (e: unknown) {
      const err = e as ApiError;
      setSubmitErr(err.detail || (e as Error).message || 'Failed to create source');
    } finally {
      setBusy(false);
    }
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
                  <label className="block text-xs text-ink-500 mb-1">Source type</label>
                  <select
                    className="input"
                    value={chosen?.name ?? ''}
                    onChange={(e) =>
                      pickPlugin((plugins ?? []).find(p => p.name === e.target.value) ?? null)
                    }
                  >
                    <option value="">Select…</option>
                    {(plugins ?? []).map(p => (
                      <option key={p.name} value={p.name}>{p.display_name}</option>
                    ))}
                  </select>
                  {chosen?.description && (
                    <p className="text-[11px] text-ink-500 mt-1">{chosen.description}</p>
                  )}
                </div>
                <div>
                  <label className="block text-xs text-ink-500 mb-1">Display name</label>
                  <Input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder={chosen ? `My ${chosen.display_name}` : 'My source'}
                  />
                </div>
                {chosen && (
                  <div className="md:col-span-2 border-t border-ink-100 pt-4">
                    <h3 className="text-xs font-semibold text-ink-700 mb-3">
                      Configuration
                    </h3>
                    <JsonSchemaForm
                      schema={chosen.config_schema as any}
                      value={config}
                      onChange={setConfig}
                    />
                  </div>
                )}
                {submitErr && (
                  <div className="md:col-span-2 flex items-start gap-2 text-xs text-red-800 bg-red-50 border border-red-200 rounded-lg p-2">
                    <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                    <span>{submitErr}</span>
                  </div>
                )}
                <div className="md:col-span-2 flex justify-end">
                  <Button onClick={add} disabled={!chosen || busy}>
                    <Plus size={14} /> {busy ? 'Saving…' : 'Create source'}
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
