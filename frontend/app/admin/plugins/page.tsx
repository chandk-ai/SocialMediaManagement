'use client';

/**
 * Plugin marketplace — Pillar 6 admin view.
 *
 * Browse the loaded plugin registry grouped by kind: source, platform,
 * selection, trigger, review_channel, media, engagement. Click a plugin
 * to see its config_schema, required fields, description, and which
 * agent stage it plugs into.
 *
 * Customers building their own plugins use the `smms-plugin` CLI to
 * scaffold and validate; this page is the in-app catalog so an admin
 * can audit what's installed and what config each plugin expects.
 */
import { useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api/client';
import { toast } from '@/components/ui/Toast';
import { Package, Sparkles, Shield, AlertTriangle, ChevronRight } from 'lucide-react';

type Plugin = {
  kind: string; name: string;
  display_name: string; description: string;
  api_version: string; category: string;
  needs_llm: boolean; experimental: boolean;
  config_schema: any;
};

const KIND_LABEL: Record<string, string> = {
  source: 'Sources',
  platform: 'Platforms',
  selection: 'Selection strategies',
  trigger: 'Triggers',
  review_channel: 'Review channels',
  media: 'Media generators',
  engagement: 'Engagement',
  llm: 'LLM providers',
};

export default function MarketplacePage() {
  const [catalog, setCatalog] = useState<Record<string, Plugin[]>>({});
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Plugin | null>(null);

  async function load() {
    try {
      const data = await api.get<{ kinds: Record<string, Plugin[]> }>('/marketplace');
      setCatalog(data.kinds || {});
    } catch (e: any) {
      toast.error(`Failed to load catalog: ${e?.message || 'unknown'}`);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { load(); }, []);

  const total = useMemo(() => {
    return Object.values(catalog).reduce((n, arr) => n + arr.length, 0);
  }, [catalog]);

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold flex items-center gap-2"><Package size={20}/> Plugin marketplace</h1>
        <p className="text-sm text-ink-600">Pillar 6 — every plugin loaded into the registry. Use the <code className="px-1 bg-stone-100 rounded">smms-plugin</code> CLI to author + validate your own.</p>
        <p className="text-xs text-ink-500 mt-1">{total} plugins total across {Object.keys(catalog).length} kinds.</p>
      </header>

      {loading ? (
        <p className="text-sm text-ink-500 py-8 text-center">Loading…</p>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_2fr] gap-6">
          <div className="space-y-4">
            {Object.entries(catalog).map(([kind, plugins]) => (
              <div key={kind} className="rounded border bg-white">
                <h2 className="px-4 py-2 border-b text-sm font-semibold bg-stone-50">{KIND_LABEL[kind] || kind}</h2>
                <ul className="divide-y">
                  {plugins.length === 0 ? (
                    <li className="p-3 text-sm text-ink-500">No {kind} plugins loaded.</li>
                  ) : plugins.map(p => (
                    <li key={p.name}>
                      <button
                        onClick={() => setSelected(p)}
                        className={`w-full text-left p-3 hover:bg-stone-50 flex items-center gap-2 ${selected?.name === p.name && selected?.kind === p.kind ? 'bg-brand-50' : ''}`}
                      >
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium truncate flex items-center gap-1">
                            {p.display_name}
                            {p.needs_llm && <Sparkles size={12} className="text-amber-500" />}
                            {p.experimental && <AlertTriangle size={12} className="text-orange-500" />}
                            {p.category === 'custom' && <Shield size={12} className="text-blue-500" />}
                          </p>
                          <p className="text-xs text-ink-500 truncate">{p.description || 'No description'}</p>
                        </div>
                        <ChevronRight size={14} className="text-ink-300"/>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>

          <div className="rounded border bg-white p-4 sticky top-4 self-start">
            {selected ? (
              <PluginDetail plugin={selected} />
            ) : (
              <div className="p-8 text-center text-ink-500">
                <Package size={32} className="mx-auto opacity-50 mb-2"/>
                <p className="text-sm">Select a plugin to see its schema, capabilities, and config form.</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function PluginDetail({ plugin: p }: { plugin: Plugin }) {
  const [config, setConfig] = useState<Record<string, any>>({});
  const [errors, setErrors] = useState<{ path: string; msg: string }[]>([]);
  const [validating, setValidating] = useState(false);

  async function validate() {
    setValidating(true);
    try {
      const data = await api.post<{ ok: boolean; errors: { path: string; msg: string }[] }>(
        '/marketplace/validate',
        { kind: p.kind, name: p.name, config },
      );
      setErrors(data.errors || []);
      if (data.ok) toast.success('Config is valid');
    } catch (e: any) {
      toast.error(`Validation failed: ${e?.message || 'unknown'}`);
    } finally {
      setValidating(false);
    }
  }

  const props = (p.config_schema?.properties || {}) as Record<string, any>;
  const required: string[] = p.config_schema?.required || [];

  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-center gap-2 mb-1">
          <h2 className="text-base font-semibold">{p.display_name}</h2>
          <span className="text-xs text-ink-500 font-mono">{p.kind}/{p.name}</span>
        </div>
        <p className="text-sm text-ink-700">{p.description}</p>
        <div className="flex gap-2 mt-2 text-xs">
          <span className="px-2 py-0.5 bg-stone-100 rounded">api {p.api_version}</span>
          <span className="px-2 py-0.5 bg-stone-100 rounded">{p.category}</span>
          {p.needs_llm && <span className="px-2 py-0.5 bg-amber-100 text-amber-800 rounded">requires LLM</span>}
          {p.experimental && <span className="px-2 py-0.5 bg-orange-100 text-orange-800 rounded">experimental</span>}
        </div>
      </div>

      <div>
        <h3 className="text-sm font-semibold mb-2">Configuration</h3>
        {Object.keys(props).length === 0 ? (
          <p className="text-sm text-ink-500">This plugin has no configurable options.</p>
        ) : (
          <div className="space-y-3">
            {Object.entries(props).map(([key, def]: [string, any]) => (
              <div key={key}>
                <label className="block text-xs font-medium mb-1">
                  {def.title || key}
                  {required.includes(key) && <span className="text-red-600 ml-1">*</span>}
                </label>
                {def.type === 'string' && def.enum ? (
                  <select className="input w-full" value={config[key] ?? ''} onChange={e => setConfig(c => ({...c, [key]: e.target.value}))}>
                    <option value="">— select —</option>
                    {def.enum.map((v: string) => <option key={v} value={v}>{v}</option>)}
                  </select>
                ) : def.type === 'integer' || def.type === 'number' ? (
                  <input type="number" className="input w-full" min={def.minimum} max={def.maximum} value={config[key] ?? def.default ?? ''} onChange={e => setConfig(c => ({...c, [key]: Number(e.target.value)}))} />
                ) : def.type === 'boolean' ? (
                  <input type="checkbox" checked={!!config[key]} onChange={e => setConfig(c => ({...c, [key]: e.target.checked}))} />
                ) : (
                  <input type={def.format === 'password' ? 'password' : 'text'} className="input w-full" value={config[key] ?? ''} onChange={e => setConfig(c => ({...c, [key]: e.target.value}))} />
                )}
                {def.description && <p className="text-xs text-ink-500 mt-1">{def.description}</p>}
              </div>
            ))}
            <button className="btn btn-outline" onClick={validate} disabled={validating}>
              {validating ? 'Validating…' : 'Validate config'}
            </button>
          </div>
        )}

        {errors.length > 0 && (
          <ul className="mt-3 text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2 space-y-0.5">
            {errors.map((e, i) => <li key={i}><code>{e.path}</code> — {e.msg}</li>)}
          </ul>
        )}
      </div>

      <details className="text-xs">
        <summary className="cursor-pointer text-ink-600">Raw schema</summary>
        <pre className="mt-2 p-2 bg-stone-50 rounded overflow-x-auto">{JSON.stringify(p.config_schema, null, 2)}</pre>
      </details>
    </div>
  );
}
