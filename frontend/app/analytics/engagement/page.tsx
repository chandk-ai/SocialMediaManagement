'use client';

/**
 * Engagement Insights — Pillar 3 dashboard.
 *
 * Shows attribution rankings across the dimensions that drive operator
 * decisions:
 *   - Source: which feed produces engagement?
 *   - Strategy: which selection strategy paid off?
 *   - Platform: where do you get the best ROI?
 *   - Hour: what time of day works?
 *   - Weekday: what day works?
 *   - Workflow: which workflows deliver?
 *
 * Each tab pulls /engagement/learnings/{dim} and renders a sortable bar
 * chart + a sample-size column so an operator can read past confidence.
 *
 * The "Recent snapshots" tab pulls /engagement/recent — useful for
 * spot-checking whether the fetcher is keeping up.
 */
import { useEffect, useMemo, useState } from 'react';
import { fetchJSON } from '@/lib/api';
import { showToast } from '@/components/Toast';
import { Activity, BarChart3, RefreshCw, TrendingUp } from 'lucide-react';

type Result = {
  key: string; avg_engagement: number;
  p50: number; p90: number; sample_size: number;
};

const DIMS: Array<{ key: string; label: string; help: string }> = [
  { key: 'source', label: 'By Source', help: 'Which feed produces the highest engagement?' },
  { key: 'strategy', label: 'By Strategy', help: 'Which selection strategy paid off?' },
  { key: 'platform', label: 'By Platform', help: 'Where do you get the best ROI?' },
  { key: 'hour', label: 'By Hour (UTC)', help: 'What time of day works?' },
  { key: 'weekday', label: 'By Weekday', help: 'What day works?' },
  { key: 'workflow', label: 'By Workflow', help: 'Which workflows deliver?' },
];

export default function EngagementAnalyticsPage() {
  const [dim, setDim] = useState<string>('source');
  const [results, setResults] = useState<Result[]>([]);
  const [snapshots, setSnapshots] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showRecent, setShowRecent] = useState(false);
  const [aggregating, setAggregating] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const data = await fetchJSON(`/api/v1/engagement/learnings/${dim}?limit=30`);
      setResults((data.results || []) as Result[]);
    } catch (e: any) {
      showToast({ title: 'Failed to load learnings', body: e?.message || 'unknown', tone: 'error' });
    } finally {
      setLoading(false);
    }
  }

  async function loadRecent() {
    try {
      const data = await fetchJSON('/api/v1/engagement/recent?limit=50');
      setSnapshots(data.snapshots || []);
    } catch (e: any) {
      showToast({ title: 'Failed to load snapshots', body: e?.message || 'unknown', tone: 'error' });
    }
  }

  async function recompute() {
    setAggregating(true);
    try {
      const data = await fetchJSON('/api/v1/engagement/aggregate', { method: 'POST' });
      showToast({ title: 'Rollup recomputed', body: `${data.rollup_rows} rows`, tone: 'success' });
      load();
    } catch (e: any) {
      showToast({ title: 'Recompute failed', body: e?.message || 'unknown', tone: 'error' });
    } finally {
      setAggregating(false);
    }
  }

  useEffect(() => { load(); }, [dim]);
  useEffect(() => { if (showRecent) loadRecent(); }, [showRecent]);

  const max = useMemo(() => Math.max(1, ...results.map(r => r.avg_engagement)), [results]);

  const dimMeta = DIMS.find(d => d.key === dim)!;

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2"><BarChart3 size={20}/> Engagement insights</h1>
          <p className="text-sm text-ink-600">Pillar 3 — what's actually working, attributed to source / strategy / time / platform.</p>
        </div>
        <div className="flex gap-2">
          <button className="btn btn-outline" onClick={load}><RefreshCw size={14}/> Refresh</button>
          <button className="btn btn-outline" onClick={recompute} disabled={aggregating}>
            <Activity size={14}/> {aggregating ? 'Recomputing…' : 'Recompute rollup'}
          </button>
        </div>
      </header>

      <div className="flex flex-wrap gap-2">
        {DIMS.map(d => (
          <button
            key={d.key}
            onClick={() => setDim(d.key)}
            className={`px-3 py-1.5 rounded-full text-sm border ${dim === d.key ? 'bg-brand-600 text-white border-brand-600' : 'bg-white border-stone-300 hover:bg-stone-50'}`}
          >
            {d.label}
          </button>
        ))}
      </div>

      <div className="rounded border bg-white p-4">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-base font-semibold flex items-center gap-2"><TrendingUp size={16}/> {dimMeta.label}</h2>
            <p className="text-xs text-ink-600">{dimMeta.help}</p>
          </div>
        </div>

        {loading ? (
          <p className="text-sm text-ink-500 py-8 text-center">Loading…</p>
        ) : results.length === 0 ? (
          <div className="text-sm text-ink-500 py-8 text-center">
            No data yet. Publish a few posts and let the engagement worker run two snapshots — then click "Recompute rollup".
          </div>
        ) : (
          <div className="space-y-2">
            {results.map((r, i) => (
              <div key={r.key + i} className="flex items-center gap-3">
                <span className="w-32 truncate text-sm font-mono" title={r.key}>{r.key}</span>
                <div className="flex-1 h-6 bg-stone-100 rounded overflow-hidden relative">
                  <div className="h-full bg-brand-500 transition-all" style={{ width: `${(r.avg_engagement / max) * 100}%` }} />
                  <span className="absolute inset-0 flex items-center px-2 text-xs">{r.avg_engagement.toFixed(2)}</span>
                </div>
                <span className="w-16 text-right text-xs text-ink-500" title={`p50 ${r.p50.toFixed(1)} • p90 ${r.p90.toFixed(1)}`}>
                  n={r.sample_size}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="rounded border bg-white">
        <button
          onClick={() => setShowRecent(s => !s)}
          className="w-full text-left px-4 py-3 text-sm font-semibold flex items-center justify-between hover:bg-stone-50"
        >
          <span>Recent snapshots</span>
          <span className="text-xs text-ink-500">{showRecent ? 'Hide' : 'Show'}</span>
        </button>
        {showRecent && (
          <div className="overflow-x-auto border-t">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-ink-600 bg-stone-50">
                <tr>
                  <th className="p-2">When</th>
                  <th className="p-2">Post</th>
                  <th className="p-2">Likes</th>
                  <th className="p-2">Comments</th>
                  <th className="p-2">Shares</th>
                  <th className="p-2">Impr.</th>
                  <th className="p-2">Clicks</th>
                  <th className="p-2">Plays / Watch</th>
                </tr>
              </thead>
              <tbody>
                {snapshots.map(s => (
                  <tr key={s.id} className="border-t">
                    <td className="p-2 text-xs">{s.snapshotted_at ? new Date(s.snapshotted_at).toLocaleString() : '—'}</td>
                    <td className="p-2 font-mono text-xs">{(s.post_id || '').slice(0, 8)}</td>
                    <td className="p-2">{s.likes ?? '—'}</td>
                    <td className="p-2">{s.comments ?? '—'}</td>
                    <td className="p-2">{s.shares ?? '—'}</td>
                    <td className="p-2">{s.impressions ?? '—'}</td>
                    <td className="p-2">{s.clicks ?? '—'}</td>
                    <td className="p-2">{s.plays ?? s.watch_time_s ?? '—'}</td>
                  </tr>
                ))}
                {snapshots.length === 0 && (
                  <tr><td colSpan={8} className="p-4 text-center text-ink-500">No snapshots yet.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
