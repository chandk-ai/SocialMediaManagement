'use client';

/**
 * Plugin marketplace — Pillar 6 admin view.
 *
 * Browse the loaded plugin registry grouped by kind: source, platform,
 * selection, trigger, review_channel, media, engagement, llm. Click a
 * plugin to see its config_schema, required fields, description, and
 * usage stats (which workflows reference it).
 *
 * Operator workflow:
 *   * Cross-kind search at the top — type "anthropic" or "rss" or
 *     "linkedin" and only matching plugins show across every group.
 *   * Toggle kinds in the rail to collapse groups you don't care about.
 *   * Detail pane shows the schema with a live validator, plus the
 *     install instructions for shipping a custom one.
 *
 * Authoring own plugins goes through the `smms-plugin` CLI (instructions
 * panel inside the detail). Drop the resulting directory under
 * `backend/app/plugins/` and restart — the marketplace picks it up.
 */
import { useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api/client';
import { toast } from '@/components/ui/Toast';
import { AppShell } from '@/components/layout/AppShell';
import {
  Package, Sparkles, Shield, AlertTriangle, ChevronRight, Search,
  ChevronDown, ChevronUp, Terminal,
} from 'lucide-react';

type Plugin = {
  kind: string; name: string;
  display_name: string; description: string;
  api_version: string; category: string;
  needs_llm: boolean; experimental: boolean;
  config_schema: any;
};

type Workflow = {
  id: string;
  config?: {
    llm_provider?: string;
    selection_strategy?: string;
  };
  source_ids?: string[];
  platform_ids?: string[];
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
  const [search, setSearch] = useState('');
  const [usage, setUsage] = useState<Record<string, number>>({});
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

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

  async function loadUsage() {
    try {
      // Back-reference count: which plugins are actively referenced by
      // a workflow / source / platform. Cheap aggregate so the detail
      // pane can show "in use by N workflows" without round-trips.
      const wfs = await api.get<{ workflows: Workflow[] }>('/workflows?limit=500')
        .catch(() => ({ workflows: [] as Workflow[] }));
      const counts: Record<string, number> = {};
      for (const wf of wfs.workflows || []) {
        const llm = wf.config?.llm_provider;
        const strat = wf.config?.selection_strategy;
        if (llm) counts[`llm/${llm}`] = (counts[`llm/${llm}`] || 0) + 1;
        if (strat) counts[`selection/${strat}`] = (counts[`selection/${strat}`] || 0) + 1;
      }
      // Sources + platforms — count distinct plugin_name across rows.
      try {
        const srcs = await api.get<{ sources: any[] }>('/sources?limit=500');
        for (const s of srcs.sources || []) {
          const n = s.plugin_name; if (n) counts[`source/${n}`] = (counts[`source/${n}`] || 0) + 1;
        }
      } catch { /* sources unavailable, skip */ }
      try {
        const pls = await api.get<{ platforms: any[] }>('/platforms?limit=500');
        for (const p of pls.platforms || []) {
          const n = p.kind || p.plugin_name;
          if (n) counts[`platform/${n}`] = (counts[`platform/${n}`] || 0) + 1;
        }
      } catch { /* platforms unavailable, skip */ }
      setUsage(counts);
    } catch {
      // Usage is best-effort enrichment, never fatal.
    }
  }

  useEffect(() => { load(); loadUsage(); }, []);

  const total = useMemo(
    () => Object.values(catalog).reduce((n, arr) => n + arr.length, 0),
    [catalog],
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return catalog;
    const out: Record<string, Plugin[]> = {};
    for (const [kind, plugins] of Object.entries(catalog)) {
      const hits = plugins.filter(p =>
        p.name.toLowerCase().includes(q)
        || p.display_name.toLowerCase().includes(q)
        || (p.description || '').toLowerCase().includes(q),
      );
      if (hits.length) out[kind] = hits;
    }
    return out;
  }, [catalog, search]);

  return (
    <AppShell>
      <div className="p-6 max-w-6xl mx-auto">
        <header className="mb-4">
          <p className="text-sm text-ink-600">
            Pillar 6 — every plugin loaded into the registry. Use the
            <code className="px-1 mx-1 bg-stone-100 rounded">smms-plugin</code>
            CLI to author + validate your own.
          </p>
          <p className="text-xs text-ink-500 mt-1">
            {total} plugins total across {Object.keys(catalog).length} kinds.
          </p>
        </header>

        <div className="mb-4 relative">
          <Search size={14} className="absolute left-3 top-2.5 text-ink-400" />
          <input
            className="input pl-8 w-full max-w-md"
            placeholder="Search plugins across all kinds…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>

        {loading ? (
          <p className="text-sm text-ink-500 py-8 text-center">Loading…</p>
        ) : Object.keys(filtered).length === 0 ? (
          <div className="rounded border bg-white p-8 text-center text-ink-500">
            <Package size={28} className="mx-auto opacity-50 mb-2"/>
            <p className="text-sm">No plugins match "{search}".</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_2fr] gap-6">
            <div className="space-y-3">
              {Object.entries(filtered).map(([kind, plugins]) => (
                <div key={kind} className="rounded border bg-white">
                  <button
                    onClick={() => setCollapsed(c => ({ ...c, [kind]: !c[kind] }))}
                    className="w-full px-4 py-2 border-b text-sm font-semibold bg-stone-50 flex items-center justify-between hover:bg-stone-100"
                  >
                    <span>{KIND_LABEL[kind] || kind}</span>
                    <span className="text-xs text-ink-500 inline-flex items-center gap-1">
                      {plugins.length}
                      {collapsed[kind] ? <ChevronDown size={14}/> : <ChevronUp size={14}/>}
                    </span>
                  </button>
                  {!collapsed[kind] && (
                    <ul className="divide-y">
                      {plugins.map(p => {
                        const usageCount = usage[`${p.kind}/${p.name}`] || 0;
                        const active = selected?.name === p.name && selected?.kind === p.kind;
                        return (
                          <li key={p.name}>
                            <button
                              onClick={() => setSelected(p)}
                              className={`w-full text-left p-3 hover:bg-stone-50 flex items-center gap-2 ${active ? 'bg-brand-50' : ''}`}
                            >
                              <div className="flex-1 min-w-0">
                                <p className="text-sm font-medium truncate flex items-center gap-1.5">
                                  {p.display_name || p.name}
                                  {p.needs_llm && <Sparkles size={12} className="text-amber-500" aria-label="Requires LLM"/>}
                                  {p.experimental && <AlertTriangle size={12} className="text-orange-500" aria-label="Experimental"/>}
                                  {p.category === 'custom' && <Shield size={12} className="text-blue-500" aria-label="Custom plugin"/>}
                                </p>
                                <p className="text-xs text-ink-500 truncate">
                                  {p.description || <span className="italic">No description</span>}
                                </p>
                              </div>
                              <div className="flex flex-col items-end gap-1 shrink-0">
                                {usageCount > 0 && (
                                  <span className="text-[10px] px-1.5 py-0.5 bg-green-100 text-green-800 rounded" title={`Used by ${usageCount} workflow${usageCount === 1 ? '' : 's'} / source / platform`}>
                                    in use × {usageCount}
                                  </span>
                                )}
                                <ChevronRight size={14} className="text-ink-300"/>
                              </div>
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
              ))}
            </div>

            <div className="rounded border bg-white p-4 sticky top-4 self-start">
              {selected ? (
                <PluginDetail plugin={selected} usageCount={usage[`${selected.kind}/${selected.name}`] || 0} />
              ) : (
                <EmptyDetail />
              )}
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}

function EmptyDetail() {
  return (
    <div className="p-8 text-center text-ink-500">
      <Package size={32} className="mx-auto opacity-50 mb-2"/>
      <p className="text-sm font-medium">Select a plugin</p>
      <p className="text-xs mt-1">
        See its schema, capabilities, config form, and which workflows use it.
      </p>
    </div>
  );
}

function PluginDetail({ plugin: p, usageCount }: { plugin: Plugin; usageCount: number }) {
  const [config, setConfig] = useState<Record<string, any>>({});
  const [errors, setErrors] = useState<{ path: string; msg: string }[]>([]);
  const [validating, setValidating] = useState(false);
  const [showInstall, setShowInstall] = useState(false);

  // Reset config when switching plugins.
  useEffect(() => { setConfig({}); setErrors([]); }, [p.kind, p.name]);

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
          <h2 className="text-base font-semibold">{p.display_name || p.name}</h2>
          <span className="text-xs text-ink-500 font-mono">{p.kind}/{p.name}</span>
        </div>
        <p className="text-sm text-ink-700">{p.description || <span className="italic text-ink-500">No description</span>}</p>
        <div className="flex flex-wrap gap-2 mt-2 text-xs">
          <span className="px-2 py-0.5 bg-stone-100 rounded">api {p.api_version}</span>
          <span className="px-2 py-0.5 bg-stone-100 rounded">{p.category}</span>
          {p.needs_llm && <span className="px-2 py-0.5 bg-amber-100 text-amber-800 rounded">requires LLM</span>}
          {p.experimental && <span className="px-2 py-0.5 bg-orange-100 text-orange-800 rounded">experimental</span>}
          {usageCount > 0 && (
            <span className="px-2 py-0.5 bg-green-100 text-green-800 rounded">
              in use × {usageCount}
            </span>
          )}
        </div>
      </div>

      <div>
        <h3 className="text-sm font-semibold mb-2">Configuration</h3>
        {Object.keys(props).length === 0 ? (
          <p className="text-sm text-ink-500">This plugin has no configurable options.</p>
        ) : (
          <div className="space-y-3">
            {Object.entries(props).map(([key, def]: [string, any]) => (
              <SchemaField
                key={key} name={key} def={def}
                required={required.includes(key)}
                value={config[key]}
                onChange={v => setConfig(c => ({ ...c, [key]: v }))}
              />
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

      <details className="text-xs" onToggle={e => setShowInstall((e.target as HTMLDetailsElement).open)}>
        <summary className="cursor-pointer text-ink-600 inline-flex items-center gap-1">
          <Terminal size={12}/> Author a similar plugin
        </summary>
        {showInstall && (
          <pre className="mt-2 p-2 bg-stone-50 border rounded text-xs overflow-x-auto whitespace-pre-wrap break-words">{`# Scaffold
python backend/scripts/smms_plugin.py init ${p.kind} \\
    --name my_${p.kind} --display "My ${p.display_name || p.name}"

# Edit ./plugins/my_${p.kind}/plugin.py with your fetch / publish logic.

# Validate before drop-in
python backend/scripts/smms_plugin.py validate ./plugins/my_${p.kind}

# Install: copy the directory under backend/app/plugins/, restart the API
# and worker. The plugin appears here automatically with this same schema
# form.`}</pre>
        )}
      </details>

      <details className="text-xs">
        <summary className="cursor-pointer text-ink-600">Raw config schema</summary>
        <pre className="mt-2 p-2 bg-stone-50 rounded overflow-x-auto">{JSON.stringify(p.config_schema, null, 2)}</pre>
      </details>
    </div>
  );
}

function SchemaField({ name, def, required, value, onChange }: {
  name: string; def: any; required: boolean; value: any; onChange: (v: any) => void;
}) {
  return (
    <div>
      <label className="block text-xs font-medium mb-1">
        {def.title || name}
        {required && <span className="text-red-600 ml-1">*</span>}
      </label>
      {def.type === 'string' && def.enum ? (
        <select className="input w-full" value={value ?? ''} onChange={e => onChange(e.target.value)}>
          <option value="">— select —</option>
          {def.enum.map((v: string) => <option key={v} value={v}>{v}</option>)}
        </select>
      ) : def.type === 'integer' || def.type === 'number' ? (
        <input type="number" className="input w-full" min={def.minimum} max={def.maximum}
          value={value ?? def.default ?? ''}
          onChange={e => onChange(Number(e.target.value))} />
      ) : def.type === 'boolean' ? (
        <input type="checkbox" checked={!!value} onChange={e => onChange(e.target.checked)} />
      ) : (
        <input type={def.format === 'password' ? 'password' : 'text'} className="input w-full"
          value={value ?? ''} onChange={e => onChange(e.target.value)} />
      )}
      {def.description && (
        <p className="text-xs text-ink-500 mt-1">{def.description}</p>
      )}
    </div>
  );
}
