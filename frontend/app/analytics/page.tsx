'use client';
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { useApi } from '@/lib/api/client';
import type { Post, Platform } from '@/lib/api/types';
import { Send, AlertTriangle, CheckCircle2, Clock } from 'lucide-react';
import { useMemo } from 'react';

export default function AnalyticsPage() {
  const { data: posts } = useApi<Post[]>('/posts');
  const { data: platforms } = useApi<Platform[]>('/platforms');

  const stats = useMemo(() => computeStats(posts ?? []), [posts]);
  const byPlatform = useMemo(
    () => computeByPlatform(posts ?? [], platforms ?? []),
    [posts, platforms],
  );
  const byDay = useMemo(() => computeByDay(posts ?? []), [posts]);

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Analytics" />
        <main className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Top-line stats */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <StatCard icon={<Send size={16} />} label="Published (last 30d)" value={stats.published30d} />
            <StatCard icon={<Clock size={16} />} label="Scheduled" value={stats.scheduled} />
            <StatCard icon={<AlertTriangle size={16} />} label="Failed" value={stats.failed} tone="warning" />
            <StatCard icon={<CheckCircle2 size={16} />} label="Approval rate" value={`${stats.approvalRate}%`} />
          </div>

          {/* By platform */}
          <Card>
            <CardTitle>By platform</CardTitle>
            <CardDescription>Posts per connected platform · last 30 days</CardDescription>
            <div className="mt-4 space-y-2">
              {byPlatform.length === 0 && (
                <p className="text-sm text-ink-500">No published posts yet.</p>
              )}
              {byPlatform.map(({ name, total, success, failed }) => (
                <div key={name} className="grid grid-cols-12 items-center gap-3 text-sm">
                  <div className="col-span-3 truncate">{name}</div>
                  <div className="col-span-7 h-2 rounded-full bg-ink-100 overflow-hidden flex">
                    <div className="h-full bg-emerald-400" style={{ width: `${pct(success, total)}%` }} />
                    <div className="h-full bg-red-400"     style={{ width: `${pct(failed, total)}%` }} />
                  </div>
                  <div className="col-span-2 text-right font-mono text-xs text-ink-500">
                    {success}/{total}
                  </div>
                </div>
              ))}
            </div>
          </Card>

          {/* By day */}
          <Card>
            <CardTitle>Throughput</CardTitle>
            <CardDescription>Posts created per day · last 14 days</CardDescription>
            <div className="mt-4 grid grid-cols-14 gap-1 h-32 items-end">
              {byDay.map(({ date, count }) => (
                <div key={date} className="flex flex-col items-center gap-1">
                  <div
                    className="w-full bg-accent rounded-t"
                    style={{ height: `${Math.max(8, (count / Math.max(...byDay.map(d => d.count), 1)) * 100)}%` }}
                    title={`${date}: ${count} posts`}
                  />
                  <div className="text-[10px] text-ink-500">{date.slice(5)}</div>
                </div>
              ))}
            </div>
          </Card>

          {/* Status breakdown */}
          <Card>
            <CardTitle>Status mix</CardTitle>
            <div className="mt-4 flex flex-wrap gap-2">
              {Object.entries(stats.statusCounts).map(([s, n]) => (
                <Badge
                  key={s}
                  tone={s === 'published' ? 'success' : s === 'failed' ? 'danger' : s === 'review' ? 'warning' : 'default'}
                >
                  {s}: {n}
                </Badge>
              ))}
            </div>
          </Card>
        </main>
      </div>
    </div>
  );
}

function StatCard({ icon, label, value, tone }: { icon: React.ReactNode; label: string; value: number | string; tone?: string }) {
  return (
    <Card className="py-4">
      <div className="flex items-center gap-2 text-ink-500 text-xs">{icon} {label}</div>
      <div className={`mt-2 text-2xl font-semibold tracking-tight ${tone === 'warning' ? 'text-amber-600' : ''}`}>
        {value}
      </div>
    </Card>
  );
}

function computeStats(posts: Post[]) {
  const now = Date.now();
  const month = 30 * 24 * 60 * 60 * 1000;
  const published30d = posts.filter(p =>
    p.published_at && now - new Date(p.published_at).getTime() < month
  ).length;
  const scheduled = posts.filter(p => p.status === 'scheduled').length;
  const failed = posts.filter(p => p.status === 'failed').length;
  const approved = posts.filter(p => ['approved', 'published'].includes(p.status)).length;
  const approvalRate = posts.length ? Math.round((approved / posts.length) * 100) : 0;
  const statusCounts: Record<string, number> = {};
  for (const p of posts) statusCounts[p.status] = (statusCounts[p.status] || 0) + 1;
  return { published30d, scheduled, failed, approvalRate, statusCounts };
}

function computeByPlatform(posts: Post[], platforms: Platform[]) {
  const lookup = new Map(platforms.map(p => [p.id, p]));
  const map = new Map<string, { name: string; total: number; success: number; failed: number }>();
  for (const p of posts) {
    const plat = lookup.get(p.platform_id);
    const key = plat ? `${plat.plugin_name} · ${plat.display_name}` : 'unknown';
    const cur = map.get(key) ?? { name: key, total: 0, success: 0, failed: 0 };
    cur.total++;
    if (p.status === 'published') cur.success++;
    if (p.status === 'failed') cur.failed++;
    map.set(key, cur);
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

function pct(n: number, total: number) {
  return total === 0 ? 0 : Math.round((n / total) * 100);
}
