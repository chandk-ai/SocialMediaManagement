'use client';
/**
 * Analytics — rebuilt around facts users actually want to see:
 *   • KPI hero (4 cards): published this week, scheduled, awaiting review, failed
 *   • Funnel: draft → review → approved → published / failed (visual + counts)
 *   • Per-account breakdown with publish vs failure rates
 *   • Daily throughput sparkline (last 14 days)
 *   • Recent activity feed (last 10 published posts with their accounts)
 */
import { useMemo } from 'react';
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { useApi } from '@/lib/api/client';
import type { Post, PlatformGroup } from '@/lib/api/types';
import {
  Send, AlertTriangle, CheckCircle2, Clock, Sparkles, Eye,
} from 'lucide-react';
import Link from 'next/link';
import { formatDateTime } from '@/lib/utils';

export default function AnalyticsPage() {
  const { data: posts } = useApi<Post[]>('/posts');
  const { data: groups } = useApi<PlatformGroup[]>('/platforms/grouped');

  const accounts = useMemo(
    () => (groups ?? []).flatMap(g => g.accounts.map(a => ({ ...a, group_label: g.display_name }))),
    [groups],
  );
  const accountById = useMemo(
    () => new Map(accounts.map(a => [a.id, a])),
    [accounts],
  );

  const stats = useMemo(() => computeStats(posts ?? []), [posts]);
  const funnel = useMemo(() => computeFunnel(posts ?? []), [posts]);
  const byAccount = useMemo(() => computeByAccount(posts ?? [], accountById), [posts, accountById]);
  const byDay = useMemo(() => computeByDay(posts ?? []), [posts]);
  const recent = useMemo(() => recentPublished(posts ?? [], accountById), [posts, accountById]);

  const empty = (posts ?? []).length === 0;

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Analytics" />
        <main className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Hero KPIs */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <KPI
              icon={<Send size={16} />}
              label="Published this week"
              value={stats.publishedWeek}
              hint={stats.publishedDelta != null ? deltaLabel(stats.publishedDelta) : null}
            />
            <KPI
              icon={<Clock size={16} />}
              label="Scheduled"
              value={stats.scheduled}
              hint={stats.scheduled > 0 ? 'Queued in calendar' : 'Nothing queued'}
            />
            <KPI
              icon={<Sparkles size={16} />}
              label="Awaiting review"
              value={stats.awaiting}
              hint={
                stats.awaiting > 0
                  ? <Link className="underline" href="/reviews">Open Reviews →</Link>
                  : 'Inbox zero'
              }
            />
            <KPI
              icon={<AlertTriangle size={16} />}
              label="Failed (last 7d)"
              value={stats.failedWeek}
              tone={stats.failedWeek > 0 ? 'warning' : undefined}
              hint={stats.failedWeek > 0
                ? <Link className="underline" href="/posts?status=failed">Inspect →</Link>
                : 'No failures'}
            />
          </div>

          {empty && (
            <Card className="text-center py-10">
              <CardTitle>No data yet</CardTitle>
              <CardDescription>
                Run a workflow once and analytics will populate. {' '}
                <Link href="/workflows" className="underline">Open Workflows →</Link>
              </CardDescription>
            </Card>
          )}

          {!empty && (
            <>
              {/* Funnel */}
              <Card>
                <CardTitle>Pipeline</CardTitle>
                <CardDescription>How drafts move through review and publishing.</CardDescription>
                <div className="mt-4 grid grid-cols-5 gap-3">
                  {funnel.steps.map((s) => (
                    <FunnelStep key={s.label} label={s.label} count={s.count} max={funnel.max} tone={s.tone} />
                  ))}
                </div>
                <div className="mt-3 text-xs text-ink-500">
                  Conversion: {funnel.toReview}% reach review · {funnel.toPublished}% reach publish · {funnel.failureRate}% failure rate
                </div>
              </Card>

              {/* By account */}
              <Card>
                <CardTitle>By account</CardTitle>
                <CardDescription>Publish vs failure mix per connected account.</CardDescription>
                <div className="mt-4 space-y-2">
                  {byAccount.length === 0 ? (
                    <p className="text-sm text-ink-500">No published posts to break down.</p>
                  ) : byAccount.map(row => (
                    <div key={row.id} className="grid grid-cols-12 items-center gap-3 text-sm">
                      <div className="col-span-4 truncate">
                        <div className="font-medium truncate">{row.label}</div>
                        <div className="text-[11px] text-ink-500">{row.plugin_name}</div>
                      </div>
                      <div className="col-span-6 h-3 rounded-full bg-ink-100 overflow-hidden flex">
                        <div className="h-full bg-emerald-400" style={{ width: `${pct(row.published, row.total)}%` }} title={`${row.published} published`} />
                        <div className="h-full bg-amber-300"   style={{ width: `${pct(row.review, row.total)}%` }}    title={`${row.review} in review`} />
                        <div className="h-full bg-red-400"     style={{ width: `${pct(row.failed, row.total)}%` }}    title={`${row.failed} failed`} />
                      </div>
                      <div className="col-span-2 text-right font-mono text-xs text-ink-500">
                        {row.published}/{row.total}
                      </div>
                    </div>
                  ))}
                </div>
              </Card>

              {/* Daily throughput */}
              <Card>
                <CardTitle>Daily throughput</CardTitle>
                <CardDescription>Posts created per day · last 14 days</CardDescription>
                <div className="mt-4 grid grid-cols-14 gap-1 h-32 items-end">
                  {byDay.map(({ date, count }) => {
                    const max = Math.max(...byDay.map(d => d.count), 1);
                    return (
                      <div key={date} className="flex flex-col items-center gap-1 group">
                        <div
                          className="w-full bg-accent rounded-t group-hover:bg-accent-fg transition-colors"
                          style={{ height: `${Math.max(8, (count / max) * 100)}%` }}
                          title={`${date}: ${count} posts`}
                        />
                        <div className="text-[10px] text-ink-500">{date.slice(5)}</div>
                      </div>
                    );
                  })}
                </div>
              </Card>

              {/* Recent activity */}
              <Card>
                <CardTitle>Recent activity</CardTitle>
                <CardDescription>Last {recent.length} published posts.</CardDescription>
                {recent.length === 0 ? (
                  <p className="text-sm text-ink-500 mt-3">Nothing published yet.</p>
                ) : (
                  <ul className="mt-3 space-y-2">
                    {recent.map(p => (
                      <li key={p.id} className="border border-ink-100 rounded p-2 text-xs">
                        <div className="flex items-center justify-between mb-1">
                          <span className="font-medium text-ink-900">{p.account_label}</span>
                          <span className="text-ink-500">{p.published_at ? formatDateTime(p.published_at) : '—'}</span>
                        </div>
                        <div className="text-ink-700 line-clamp-2">{p.text}</div>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </>
          )}
        </main>
      </div>
    </div>
  );
}

/* ───────── Small components ─────────────────────────────────────────── */

function KPI({ icon, label, value, hint, tone }: {
  icon: React.ReactNode;
  label: string;
  value: number | string;
  hint?: React.ReactNode;
  tone?: 'warning';
}) {
  return (
    <Card className="py-4">
      <div className="flex items-center gap-2 text-ink-500 text-xs">{icon} {label}</div>
      <div className={`mt-1 text-3xl font-semibold tracking-tight ${tone === 'warning' ? 'text-amber-600' : ''}`}>
        {value}
      </div>
      {hint != null && <div className="mt-1 text-[11px] text-ink-500">{hint}</div>}
    </Card>
  );
}

function FunnelStep({ label, count, max, tone }: {
  label: string;
  count: number;
  max: number;
  tone: 'default' | 'warning' | 'success' | 'danger';
}) {
  const fillColor =
    tone === 'success' ? 'bg-emerald-400' :
    tone === 'warning' ? 'bg-amber-300' :
    tone === 'danger'  ? 'bg-red-400' :
    'bg-accent-muted';
  const h = max === 0 ? 0 : Math.max(8, Math.round((count / max) * 100));
  return (
    <div className="flex flex-col gap-1">
      <div className="h-24 rounded-lg bg-ink-50 relative overflow-hidden">
        <div className={`absolute bottom-0 inset-x-0 ${fillColor}`} style={{ height: `${h}%` }} />
        <div className="absolute inset-0 flex items-center justify-center text-base font-semibold">
          {count}
        </div>
      </div>
      <div className="text-xs text-ink-500 text-center">{label}</div>
    </div>
  );
}

/* ───────── Aggregation helpers ──────────────────────────────────────── */

function computeStats(posts: Post[]) {
  const now = Date.now();
  const week = 7 * 24 * 60 * 60 * 1000;
  const prevWeek = posts.filter(p =>
    p.published_at &&
    now - new Date(p.published_at).getTime() >= week &&
    now - new Date(p.published_at).getTime() < 2 * week
  ).length;
  const publishedWeek = posts.filter(p =>
    p.published_at && now - new Date(p.published_at).getTime() < week
  ).length;
  const scheduled = posts.filter(p => p.status === 'scheduled').length;
  const awaiting = posts.filter(p => p.status === 'review').length;
  const failedWeek = posts.filter(p =>
    p.status === 'failed' && now - new Date(p.created_at).getTime() < week
  ).length;
  const publishedDelta = prevWeek === 0 ? null : publishedWeek - prevWeek;
  return { publishedWeek, prevWeek, scheduled, awaiting, failedWeek, publishedDelta };
}

function computeFunnel(posts: Post[]) {
  const draft = posts.filter(p => p.status === 'draft').length;
  const review = posts.filter(p => p.status === 'review').length;
  const approved = posts.filter(p => p.status === 'approved' || p.status === 'scheduled').length;
  const published = posts.filter(p => p.status === 'published').length;
  const failed = posts.filter(p => p.status === 'failed').length;
  const total = posts.length;
  const max = Math.max(draft, review, approved, published, failed, 1);
  return {
    steps: [
      { label: 'Draft',     count: draft,     tone: 'default' as const },
      { label: 'Review',    count: review,    tone: 'warning' as const },
      { label: 'Approved',  count: approved,  tone: 'default' as const },
      { label: 'Published', count: published, tone: 'success' as const },
      { label: 'Failed',    count: failed,    tone: 'danger'  as const },
    ],
    max,
    toReview: total === 0 ? 0 : Math.round(((review + approved + published + failed) / total) * 100),
    toPublished: total === 0 ? 0 : Math.round((published / total) * 100),
    failureRate: total === 0 ? 0 : Math.round((failed / total) * 100),
  };
}

function computeByAccount(posts: Post[], accounts: Map<string, any>) {
  const map = new Map<string, {
    id: string; label: string; plugin_name: string;
    total: number; published: number; review: number; failed: number;
  }>();
  for (const p of posts) {
    const acct = accounts.get(p.platform_id);
    const id = p.platform_id;
    const cur = map.get(id) ?? {
      id,
      label: acct?.display_name ?? '(removed account)',
      plugin_name: acct?.plugin_name ?? '—',
      total: 0, published: 0, review: 0, failed: 0,
    };
    cur.total++;
    if (p.status === 'published') cur.published++;
    if (p.status === 'review')    cur.review++;
    if (p.status === 'failed')    cur.failed++;
    map.set(id, cur);
  }
  return Array.from(map.values()).sort((a, b) => b.total - a.total);
}

function computeByDay(posts: Post[]) {
  const days: { date: string; count: number }[] = [];
  for (let i = 13; i >= 0; i--) {
    const d = new Date(); d.setDate(d.getDate() - i);
    days.push({ date: d.toISOString().slice(0, 10), count: 0 });
  }
  for (const p of posts) {
    const day = (p.published_at || p.created_at).slice(0, 10);
    const slot = days.find(x => x.date === day);
    if (slot) slot.count++;
  }
  return days;
}

function recentPublished(posts: Post[], accounts: Map<string, any>) {
  return posts
    .filter(p => p.status === 'published' && p.published_at)
    .sort((a, b) => +new Date(b.published_at!) - +new Date(a.published_at!))
    .slice(0, 10)
    .map(p => ({
      ...p,
      account_label: accounts.get(p.platform_id)?.display_name ?? '(removed account)',
    }));
}

function pct(n: number, total: number) {
  return total === 0 ? 0 : Math.round((n / total) * 100);
}

function deltaLabel(d: number): React.ReactNode {
  if (d === 0) return 'same as last week';
  const sign = d > 0 ? '+' : '';
  const cls = d > 0 ? 'text-emerald-600' : 'text-red-600';
  return <span className={cls}>{sign}{d} vs last week</span>;
}
