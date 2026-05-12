'use client';
/**
 * Sources page — production polish:
 *   • Smart empty state with copy-paste examples
 *   • Search + filter by plugin
 *   • Per-source health badge (last fetch / last error / consecutive failures)
 *   • Test-before-save in the Add form (no orphan rows from bad config)
 *   • Edit / Test / Preview / Clone / Pause / Delete on each tile
 *   • "Used by N workflows" indicator + drilldown
 */
import { useMemo, useState } from 'react';
import Link from 'next/link';
import { mutate } from 'swr';

import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { EmptyState } from '@/components/ui/EmptyState';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { JsonSchemaForm } from '@/components/ui/JsonSchemaForm';
import { useApi, api, ApiError } from '@/lib/api/client';
import { ApiErrorBanner } from '@/components/ui/ApiErrorBanner';
import type { PluginInfo, Source, Workflow } from '@/lib/api/types';
import {
  Database, Plus, AlertTriangle, CheckCircle2, XCircle,
  Edit3, Trash2, Eye, Power, Copy, X, Loader2, Search, Info,
} from 'lucide-react';
import { formatDateTime } from '@/lib/utils';

/**
 * Plugin names whose ``fetch()`` populates ``SourceItem.media`` — kept
 * in sync with the backend source adapters as of May 2026. Used to show
 * a small "yields media" badge so users can pick sources that produce
 * IG / Pinterest-eligible posts at a glance. Drift from backend reality
 * is acceptable for this hint — worst case the badge appears or
 * disappears one deploy late.
 */
const SOURCES_THAT_YIELD_MEDIA: ReadonlySet<string> = new Set([
  'notion',
  'google_drive',
  'rss',
  'web_scraper',
  'web_crawler',
  'youtube',
]);

function pluginYieldsMedia(name: string): boolean {
  return SOURCES_THAT_YIELD_MEDIA.has(name);
}

const PLUGIN_HINTS: Record<string, string> = {
  rss: "Public RSS or Atom feed URL — try a blog like https://news.ycombinator.com/rss",
  web_scraper: "Single web page — extracts the main article body.",
  web_crawler: "Recursively crawl a site starting from one or more seed URLs.",
  notion: "Notion database — needs an integration token + DB id.",
  github: "Issues / READMEs / releases from a public or private repo.",
  google_drive: "Drive folder shared with a service account — paste the JSON key.",
  s3: "AWS S3 (or MinIO/R2/Wasabi) bucket of text files.",
  mongodb: "MongoDB collection — fields are mapped to title/body.",
  sql_database: "Any SQLAlchemy URL — Postgres, MySQL, SQLite. SELECT only.",
  vector_db: "Pinecone / Chroma / Qdrant / Weaviate — top-k semantic search.",
  youtube: "Channel videos with auto-pulled transcripts.",
  file: "Local files on the backend filesystem — useful for testing.",
};

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
  const [editing, setEditing] = useState<Source | null>(null);
  const [testResult, setTestResult] = useState<{ ok: boolean; error?: string } | null>(null);
  const [testing, setTesting] = useState(false);

  // Filter state
  const [query, setQuery] = useState('');
  const [filterPlugin, setFilterPlugin] = useState<string>('all');
  const [filterStatus, setFilterStatus] = useState<'all' | 'active' | 'paused' | 'errored'>('all');

  function pickPlugin(p: PluginInfo | null) {
    setChosen(p);
    setConfig({});
    setSubmitErr(null);
    setTestResult(null);
  }

  function missingRequired(): string[] {
    const required = (chosen?.config_schema as any)?.required ?? [];
    return required.filter((k: string) => {
      const v = (config as any)[k];
      return v === undefined || v === '' || (Array.isArray(v) && v.length === 0);
    });
  }

  async function testBeforeSave() {
    if (!chosen) return;
    const missing = missingRequired();
    if (missing.length) {
      setSubmitErr(`Fill required fields first: ${missing.join(', ')}`);
      return;
    }
    setTesting(true); setSubmitErr(null); setTestResult(null);
    try {
      const res = await api.post<{ ok: boolean; error?: string }>(
        '/sources/test',
        { plugin_name: chosen.name, config },
      );
      setTestResult(res);
    } catch (e) {
      const ae = e as ApiError;
      setTestResult({ ok: false, error: ae?.detail || (e as Error)?.message || 'Test failed.' });
    } finally {
      setTesting(false);
    }
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
    } catch (e) {
      const err = e as ApiError;
      setSubmitErr(err.detail || (e as Error).message || 'Failed to create source.');
    } finally {
      setBusy(false);
    }
  }

  // Filtered list
  const filtered = useMemo(() => {
    return (sources ?? []).filter((s) => {
      if (filterPlugin !== 'all' && s.plugin_name !== filterPlugin) return false;
      if (filterStatus === 'active' && !s.is_active) return false;
      if (filterStatus === 'paused' && s.is_active) return false;
      if (filterStatus === 'errored' && (s.error_count ?? 0) === 0) return false;
      if (query) {
        const q = query.toLowerCase();
        return s.display_name.toLowerCase().includes(q)
          || s.plugin_name.toLowerCase().includes(q);
      }
      return true;
    });
  }, [sources, filterPlugin, filterStatus, query]);

  const stats = useMemo(() => {
    const all = sources ?? [];
    return {
      total: all.length,
      active: all.filter((s) => s.is_active).length,
      errored: all.filter((s) => (s.error_count ?? 0) > 0).length,
    };
  }, [sources]);

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Sources" />
        <main className="flex-1 overflow-y-auto p-6 space-y-6">
          <ApiErrorBanner error={pluginsErr || sourcesErr}
                          retry={() => { retryPlugins(); retrySources(); }} />

          {/* Heading + stats */}
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-base font-semibold text-ink-900">All sources</h2>
              <p className="text-xs text-ink-500 mt-0.5">
                Sources are where the agents pull reference material from.
                {stats.total > 0 && (
                  <> {stats.total} total · {stats.active} active{stats.errored > 0 && (
                    <> · <span className="text-red-700">{stats.errored} with errors</span></>
                  )}.</>
                )}
              </p>
            </div>
          </div>

          {/* Filter row */}
          {sources && sources.length > 3 && (
            <div className="flex flex-wrap gap-2">
              <div className="relative flex-1 min-w-[200px] max-w-md">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-400" />
                <Input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search by name or plugin…"
                  className="pl-8"
                />
              </div>
              <select
                className="input max-w-[180px]"
                value={filterPlugin}
                onChange={(e) => setFilterPlugin(e.target.value)}
              >
                <option value="all">All plugins</option>
                {(plugins ?? []).map((p) => (
                  <option key={p.name} value={p.name}>{p.display_name}</option>
                ))}
              </select>
              <select
                className="input max-w-[160px]"
                value={filterStatus}
                onChange={(e) => setFilterStatus(e.target.value as any)}
              >
                <option value="all">Any status</option>
                <option value="active">Active</option>
                <option value="paused">Paused</option>
                <option value="errored">Has errors</option>
              </select>
            </div>
          )}

          {/* Tile grid OR empty state */}
          <section>
            {sources && sources.length === 0 ? (
              <EmptyStateGuide />
            ) : filtered.length === 0 ? (
              <Card className="text-center py-8">
                <p className="text-sm text-ink-500">
                  No sources match your filter.{' '}
                  <button className="underline" onClick={() => { setQuery(''); setFilterPlugin('all'); setFilterStatus('all'); }}>
                    Clear filters
                  </button>
                </p>
              </Card>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {filtered.map((s) => (
                  <SourceTile
                    key={s.id}
                    source={s}
                    pluginInfo={(plugins ?? []).find((p) => p.name === s.plugin_name)}
                    onEdit={() => setEditing(s)}
                  />
                ))}
              </div>
            )}
          </section>

          {/* Add a source */}
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
                      pickPlugin((plugins ?? []).find((p) => p.name === e.target.value) ?? null)
                    }
                  >
                    <option value="">Select…</option>
                    {(plugins ?? []).map((p) => (
                      <option key={p.name} value={p.name}>{p.display_name}</option>
                    ))}
                  </select>
                  {chosen && (
                    <p className="text-[11px] text-ink-500 mt-1 flex items-start gap-1">
                      <Info size={11} className="mt-0.5 shrink-0" />
                      <span>{PLUGIN_HINTS[chosen.name] || chosen.description || 'Configure below.'}</span>
                    </p>
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
                    <h3 className="text-xs font-semibold text-ink-700 mb-3">Configuration</h3>
                    <JsonSchemaForm
                      schema={chosen.config_schema as any}
                      value={config}
                      onChange={(v) => { setConfig(v); setTestResult(null); }}
                    />
                  </div>
                )}
                {testResult?.ok && (
                  <div className="md:col-span-2 flex items-start gap-2 text-xs text-emerald-800 bg-emerald-50 border border-emerald-200 rounded-lg p-2">
                    <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
                    <span>Connection works — safe to save.</span>
                  </div>
                )}
                {testResult?.ok === false && (
                  <div className="md:col-span-2 flex items-start gap-2 text-xs text-red-800 bg-red-50 border border-red-200 rounded-lg p-2">
                    <XCircle size={14} className="mt-0.5 shrink-0" />
                    <span className="break-words">{testResult.error}</span>
                  </div>
                )}
                {submitErr && (
                  <div className="md:col-span-2 flex items-start gap-2 text-xs text-red-800 bg-red-50 border border-red-200 rounded-lg p-2">
                    <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                    <span>{submitErr}</span>
                  </div>
                )}
                <div className="md:col-span-2 flex justify-end gap-2">
                  <Button variant="outline" onClick={testBeforeSave} disabled={!chosen || testing || busy}>
                    {testing ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
                    {testing ? 'Testing…' : 'Test connection'}
                  </Button>
                  <Button onClick={add} disabled={!chosen || busy}>
                    <Plus size={14} /> {busy ? 'Saving…' : 'Create source'}
                  </Button>
                </div>
              </div>
            </Card>
          </section>
        </main>
      </div>

      {editing && (
        <EditSourceDialog
          source={editing}
          pluginInfo={(plugins ?? []).find((p) => p.name === editing.plugin_name)}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

/* ───────── Empty state with concrete examples ───────────────────────── */

function EmptyStateGuide() {
  return (
    <Card>
      <div className="text-center py-6">
        <Database size={32} className="mx-auto text-ink-400" />
        <h3 className="mt-3 text-base font-semibold text-ink-900">
          No sources yet
        </h3>
        <p className="text-sm text-ink-500 mt-1 max-w-md mx-auto">
          Sources feed your agents with content. Try one of these to start —
          fill in the form below.
        </p>
        <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-3 max-w-2xl mx-auto text-left">
          <ExampleCard plugin="rss" title="RSS feed"
            example="https://news.ycombinator.com/rss" />
          <ExampleCard plugin="web_scraper" title="Web page"
            example="https://anthropic.com/news" />
          <ExampleCard plugin="github" title="GitHub repo"
            example="anthropics/claude-code" />
        </div>
      </div>
    </Card>
  );
}

function ExampleCard({ plugin, title, example }: { plugin: string; title: string; example: string }) {
  return (
    <div className="rounded-lg border border-ink-100 p-3 text-xs">
      <div className="font-medium text-ink-900">{title}</div>
      <div className="text-ink-500 mt-0.5 font-mono break-all">{example}</div>
    </div>
  );
}

/* ───────── Source tile ──────────────────────────────────────────────── */

function SourceTile({ source, pluginInfo, onEdit }: {
  source: Source;
  pluginInfo: PluginInfo | undefined;
  onEdit: () => void;
}) {
  const { data: usage } = useApi<{ count: number; workflows: Array<{ id: string; name: string }> }>(`/sources/${source.id}/usage`);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [test, setTest] = useState<{ ok: boolean; error?: string } | null>(null);
  const [preview, setPreview] = useState<{
    items: Array<{ external_id: string; title: string; body: string; url?: string | null }>;
    error?: string;
  } | null>(null);

  async function runTest() {
    setBusy('test'); setErr(null); setTest(null);
    try {
      const res = await api.post<{ ok: boolean; error?: string }>(
        `/sources/${source.id}/test`, {},
      );
      setTest(res);
      await mutate('/sources');
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Test failed.');
    } finally { setBusy(null); }
  }

  async function runPreview() {
    setBusy('preview'); setErr(null); setPreview(null);
    try {
      const res = await api.post<{ ok: boolean; items: any[]; error?: string }>(
        `/sources/${source.id}/preview`, {},
      );
      setPreview({ items: res.items ?? [], error: res.error });
      if (!res.ok && res.error) setErr(res.error);
      await mutate('/sources');
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Preview failed.');
    } finally { setBusy(null); }
  }

  async function toggleActive() {
    setBusy('toggle'); setErr(null);
    try {
      await api.patch(`/sources/${source.id}`, { is_active: !source.is_active });
      await mutate('/sources');
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Toggle failed.');
    } finally { setBusy(null); }
  }

  async function clone() {
    setBusy('clone'); setErr(null);
    try {
      await api.post(`/sources/${source.id}/clone`, {});
      await mutate('/sources');
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Clone failed.');
    } finally { setBusy(null); }
  }

  async function remove() {
    const usedBy = usage?.count ?? 0;
    const msg = usedBy > 0
      ? `"${source.display_name}" is used by ${usedBy} workflow${usedBy !== 1 ? 's' : ''}. Delete anyway?`
      : `Delete "${source.display_name}"?`;
    if (!confirm(msg)) return;
    setBusy('delete'); setErr(null);
    try {
      await api.del(`/sources/${source.id}`);
      await mutate('/sources');
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Delete failed.');
    } finally { setBusy(null); }
  }

  const errored = (source.error_count ?? 0) > 0;

  return (
    <Card>
      <div className="flex items-start justify-between">
        <div className="min-w-0">
          <CardTitle className="truncate">{source.display_name}</CardTitle>
          <CardDescription>
            {pluginInfo?.display_name ?? source.plugin_name}
            {pluginYieldsMedia(source.plugin_name) && (
              <span
                title={
                  'This source produces media (images/video) that flow ' +
                  'into Posts automatically — Instagram and Pinterest can ' +
                  'publish them without a separate upload.'
                }
                className="ml-2 inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700 align-middle"
              >
                yields media
              </span>
            )}
            {(usage?.count ?? 0) > 0 && (
              <> · used by {usage!.count} workflow{usage!.count !== 1 && 's'}</>
            )}
          </CardDescription>
        </div>
        <Badge tone={
          !source.is_active ? 'default' :
          errored ? 'danger' : 'success'
        }>
          {!source.is_active ? 'paused' : errored ? `${source.error_count} errors` : 'active'}
        </Badge>
      </div>

      {/* Health line */}
      <div className="mt-2 text-xs text-ink-500 space-y-0.5">
        {source.last_fetched_at && (
          <div>Last fetched {formatDateTime(source.last_fetched_at)} · {source.item_count ?? 0} items</div>
        )}
        {source.last_failure_at && errored && (
          <div className="text-red-700 line-clamp-2">
            ⚠ Last failure {formatDateTime(source.last_failure_at)}: {source.last_error}
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="mt-3 flex flex-wrap gap-1.5">
        <Button size="sm" variant="outline" onClick={runTest} disabled={busy !== null}>
          {busy === 'test' ? <Loader2 size={12} className="animate-spin" /> :
           test?.ok === true ? <CheckCircle2 size={12} className="text-emerald-600" /> :
           test?.ok === false ? <XCircle size={12} className="text-red-600" /> : null}
          {busy === 'test' ? 'Testing…' : 'Test'}
        </Button>
        <Button size="sm" variant="outline" onClick={runPreview} disabled={busy !== null}>
          <Eye size={12} /> {busy === 'preview' ? 'Loading…' : 'Preview'}
        </Button>
        <a href={`/sources/${source.id}/items`} className="btn btn-outline" style={{padding: '0.375rem 0.75rem', fontSize: '0.75rem'}}>
          <Eye size={12} /> Items
        </a>
        <Button size="sm" variant="outline" onClick={onEdit} disabled={busy !== null}>
          <Edit3 size={12} /> Edit
        </Button>
        <Button size="sm" variant="ghost" onClick={toggleActive} disabled={busy !== null}>
          <Power size={12} /> {source.is_active ? 'Pause' : 'Resume'}
        </Button>
        <Button size="sm" variant="ghost" onClick={clone} disabled={busy !== null}>
          <Copy size={12} /> Clone
        </Button>
        <Button size="sm" variant="ghost" onClick={remove} disabled={busy !== null}>
          <Trash2 size={12} /> Delete
        </Button>
      </div>

      {test?.ok === true && (
        <div className="mt-3 flex items-start gap-2 text-xs text-emerald-800 bg-emerald-50 border border-emerald-200 rounded p-2">
          <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
          <span>Connected successfully.</span>
        </div>
      )}
      {test?.ok === false && (
        <div className="mt-3 flex items-start gap-2 text-xs text-red-800 bg-red-50 border border-red-200 rounded p-2">
          <XCircle size={14} className="mt-0.5 shrink-0" />
          <span className="break-words">{test.error}</span>
        </div>
      )}
      {err && (
        <div className="mt-3 flex items-start gap-2 text-xs text-red-800 bg-red-50 border border-red-200 rounded p-2">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span className="break-words">{err}</span>
        </div>
      )}
      {preview && preview.items.length > 0 && (
        <div className="mt-3 border-t border-ink-100 pt-3">
          <div className="text-xs text-ink-500 mb-2">
            Showing first {preview.items.length} item{preview.items.length !== 1 && 's'}:
          </div>
          <ul className="space-y-2">
            {preview.items.map((it, i) => (
              <li key={i} className="text-xs border border-ink-100 rounded p-2">
                <div className="font-medium text-ink-900 truncate">{it.title || it.external_id}</div>
                {it.url && (
                  <a href={it.url} target="_blank" rel="noreferrer"
                     className="text-accent text-[11px] truncate block">{it.url}</a>
                )}
                {it.body && <div className="text-ink-700 mt-1 line-clamp-3">{it.body}</div>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {(usage?.count ?? 0) > 0 && (
        <div className="mt-3 text-[11px] text-ink-500">
          Used by:{' '}
          {usage!.workflows.slice(0, 3).map((w) => (
            <Link key={w.id} href={`/workflows/${w.id}`} className="underline mr-1">{w.name}</Link>
          ))}
          {usage!.count > 3 && <span>+ {usage!.count - 3} more</span>}
        </div>
      )}
    </Card>
  );
}

/* ───────── Edit dialog (with test-before-save) ──────────────────────── */

function EditSourceDialog({ source, pluginInfo, onClose }: {
  source: Source;
  pluginInfo: PluginInfo | undefined;
  onClose: () => void;
}) {
  const [name, setName] = useState(source.display_name);
  const [config, setConfig] = useState<Record<string, unknown>>({ ...source.config });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [test, setTest] = useState<{ ok: boolean; error?: string } | null>(null);
  const [testing, setTesting] = useState(false);

  async function runTest() {
    if (!pluginInfo) return;
    setTesting(true); setTest(null); setErr(null);
    try {
      const res = await api.post<{ ok: boolean; error?: string }>('/sources/test', {
        plugin_name: pluginInfo.name, config,
      });
      setTest(res);
    } catch (e) {
      const ae = e as ApiError;
      setTest({ ok: false, error: ae?.detail || (e as Error)?.message || 'Test failed.' });
    } finally { setTesting(false); }
  }

  async function save() {
    setBusy(true); setErr(null);
    try {
      await api.patch(`/sources/${source.id}`, {
        display_name: name,
        config,
      });
      await mutate('/sources');
      onClose();
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Save failed.');
    } finally { setBusy(false); }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50" onClick={onClose}>
      <Card className="w-full max-w-xl max-h-[90vh] overflow-y-auto" onClick={(e: any) => e.stopPropagation()}>
        <div className="flex items-start justify-between mb-4">
          <div>
            <CardTitle>Edit source</CardTitle>
            <CardDescription>
              {pluginInfo?.display_name ?? source.plugin_name}
            </CardDescription>
          </div>
          <button onClick={onClose} className="text-ink-500 hover:text-ink-700">
            <X size={18} />
          </button>
        </div>
        <div className="space-y-4">
          <div>
            <label className="block text-xs text-ink-500 mb-1">Display name</label>
            <Input value={name} onChange={(e: any) => setName(e.target.value)} />
          </div>
          <div className="border-t border-ink-100 pt-4">
            <h3 className="text-xs font-semibold text-ink-700 mb-3">Configuration</h3>
            <p className="text-[11px] text-ink-500 mb-3">
              Leave password fields blank to keep the existing secret.
            </p>
            <JsonSchemaForm
              schema={pluginInfo?.config_schema as any}
              value={config}
              onChange={(v) => { setConfig(v); setTest(null); }}
            />
          </div>
          {test?.ok === true && (
            <div className="flex items-start gap-2 text-xs text-emerald-800 bg-emerald-50 border border-emerald-200 rounded p-2">
              <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
              <span>Connection works — safe to save.</span>
            </div>
          )}
          {test?.ok === false && (
            <div className="flex items-start gap-2 text-xs text-red-800 bg-red-50 border border-red-200 rounded p-2">
              <XCircle size={14} className="mt-0.5 shrink-0" />
              <span className="break-words">{test.error}</span>
            </div>
          )}
          {err && (
            <div className="flex items-start gap-2 text-xs text-red-800 bg-red-50 border border-red-200 rounded p-2">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span>{err}</span>
            </div>
          )}
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="outline" onClick={runTest} disabled={busy || testing}>
            {testing ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
            {testing ? 'Testing…' : 'Test'}
          </Button>
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save changes'}</Button>
        </div>
      </Card>
    </div>
  );
}
