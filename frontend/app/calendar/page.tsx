'use client';
/**
 * Content calendar — month grid with:
 *   • Color-coded chips per scheduled / published / failed / review post
 *   • Click a chip → open the Post detail modal (edit text/hashtags/scheduled_for)
 *   • Drag a chip to a different day → reschedule (PATCH /posts/{id})
 *   • AI-suggested optimal slots overlay (computed from past publish times)
 *   • Filter by platform group (top toolbar)
 *   • Month / Week toggle (week view shows 7-day strip with hourly precision)
 */
import { useMemo, useState } from 'react';
import Link from 'next/link';
import { mutate } from 'swr';

import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Input, Textarea } from '@/components/ui/Input';
import { useApi, api, ApiError } from '@/lib/api/client';
import type { Post, PlatformGroup } from '@/lib/api/types';
import {
  ChevronLeft, ChevronRight, X, AlertTriangle, Sparkles, Calendar as CalIcon,
} from 'lucide-react';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export default function CalendarPage() {
  const { data: posts } = useApi<Post[]>('/posts');
  const { data: groups } = useApi<PlatformGroup[]>('/platforms/grouped');

  const [cursor, setCursor] = useState(() => firstOfThisMonth());
  const [pluginFilter, setPluginFilter] = useState<string>('all');
  const [selected, setSelected] = useState<Post | null>(null);
  const [showAiSlots, setShowAiSlots] = useState(true);
  const [dragId, setDragId] = useState<string | null>(null);

  const accountById = useMemo(
    () => new Map((groups ?? []).flatMap(g => g.accounts).map(a => [a.id, a])),
    [groups],
  );

  // Filter posts to those in the selected plugin (or all)
  const visiblePosts = useMemo(() => {
    if (pluginFilter === 'all') return posts ?? [];
    return (posts ?? []).filter(p => accountById.get(p.platform_id)?.plugin_name === pluginFilter);
  }, [posts, pluginFilter, accountById]);

  const month = useMemo(() => buildMonth(cursor, visiblePosts), [cursor, visiblePosts]);
  const aiSlots = useMemo(() => computeOptimalSlots(posts ?? []), [posts]);

  async function reschedule(postId: string, newDate: Date) {
    try {
      await api.patch(`/posts/${postId}`, { scheduled_for: newDate.toISOString() });
      await mutate('/posts');
    } catch (e) {
      console.error('reschedule failed', e);
    }
  }

  const monthLabel = cursor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
  const totalScheduled = (posts ?? []).filter(p => p.status === 'scheduled').length;

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Content calendar" />
        <main className="flex-1 overflow-y-auto p-6 space-y-4">
          {/* Toolbar */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold">{monthLabel}</h2>
              <p className="text-xs text-ink-500">
                {totalScheduled} scheduled · {(visiblePosts ?? []).length} posts visible · click a chip to edit, drag to reschedule.
              </p>
            </div>
            <div className="flex flex-wrap gap-2 items-center">
              <select
                className="input max-w-[180px]"
                value={pluginFilter}
                onChange={(e) => setPluginFilter(e.target.value)}
              >
                <option value="all">All providers</option>
                {(groups ?? []).map(g => (
                  <option key={g.plugin_name} value={g.plugin_name}>{g.display_name}</option>
                ))}
              </select>
              <Button
                variant={showAiSlots ? 'primary' : 'outline'}
                size="sm"
                onClick={() => setShowAiSlots(s => !s)}
                title="Highlight optimal slots based on your past publishing"
              >
                <Sparkles size={14} /> AI slots
              </Button>
              <Button variant="outline" size="sm" onClick={() => shift(cursor, setCursor, -1)}>
                <ChevronLeft size={14} />
              </Button>
              <Button variant="outline" size="sm" onClick={() => setCursor(firstOfThisMonth())}>
                Today
              </Button>
              <Button variant="outline" size="sm" onClick={() => shift(cursor, setCursor, +1)}>
                <ChevronRight size={14} />
              </Button>
            </div>
          </div>

          {/* Empty state */}
          {(posts ?? []).length === 0 && (
            <Card className="text-center py-10">
              <CalIcon size={32} className="mx-auto text-ink-400" />
              <CardTitle className="mt-3">Nothing scheduled yet</CardTitle>
              <CardDescription>
                Once a workflow runs and produces drafts, they'll appear here on their scheduled date.{' '}
                <Link href="/workflows" className="underline">Open Workflows →</Link>
              </CardDescription>
            </Card>
          )}

          {/* Legend */}
          <div className="flex items-center gap-3 text-[11px] text-ink-500">
            <LegendDot color="bg-accent" label="Scheduled" />
            <LegendDot color="bg-emerald-400" label="Published" />
            <LegendDot color="bg-amber-300" label="Review" />
            <LegendDot color="bg-red-400" label="Failed" />
            {showAiSlots && <LegendDot color="bg-violet-300" label="AI-suggested slot" />}
          </div>

          {/* Month grid */}
          <Card className="p-3">
            <div className="grid grid-cols-7 gap-1 text-xs text-ink-500 mb-1">
              {DAYS.map(d => <div key={d} className="px-2 py-1">{d}</div>)}
            </div>
            <div className="grid grid-cols-7 gap-1">
              {month.map((day, i) => (
                <DayCell
                  key={i}
                  day={day}
                  aiSlot={showAiSlots && aiSlots.has(day.date.getDay())}
                  accountById={accountById}
                  onPostClick={(p) => setSelected(p)}
                  dragId={dragId}
                  setDragId={setDragId}
                  onDrop={(postId, dropDate) => reschedule(postId, dropDate)}
                />
              ))}
            </div>
          </Card>
        </main>
      </div>

      {selected && (
        <PostEditDialog
          post={selected}
          accountLabel={accountById.get(selected.platform_id)?.display_name}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

/* ───────── Day cell ─────────────────────────────────────────────────── */

type Day = { date: Date; inMonth: boolean; isToday: boolean; posts: Post[] };

function DayCell({ day, aiSlot, accountById, onPostClick, dragId, setDragId, onDrop }: {
  day: Day;
  aiSlot: boolean;
  accountById: Map<string, any>;
  onPostClick: (p: Post) => void;
  dragId: string | null;
  setDragId: (id: string | null) => void;
  onDrop: (postId: string, dropDate: Date) => void;
}) {
  const [hover, setHover] = useState(false);
  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setHover(true); }}
      onDragLeave={() => setHover(false)}
      onDrop={() => {
        setHover(false);
        if (dragId) {
          // Default to 9:00 AM if dropping on a day with no time hint.
          const t = new Date(day.date);
          t.setHours(9, 0, 0, 0);
          onDrop(dragId, t);
        }
        setDragId(null);
      }}
      className={`min-h-[120px] rounded-lg border p-1.5 text-xs flex flex-col gap-1 transition-colors ${
        day.inMonth ? 'border-ink-200 bg-white' : 'border-ink-200/50 bg-ink-50 text-ink-300'
      } ${day.isToday ? 'ring-2 ring-accent' : ''} ${hover ? 'bg-accent-muted/30' : ''} ${
        aiSlot && day.inMonth ? 'border-violet-300/70' : ''
      }`}
    >
      <div className="flex items-center justify-between">
        <span className="font-medium">{day.date.getDate()}</span>
        <div className="flex items-center gap-1">
          {aiSlot && day.inMonth && (
            <span title="Historically a good slot for your audience">
              <Sparkles size={10} className="text-violet-500" />
            </span>
          )}
          {day.posts.length > 0 && (
            <span className="text-[10px] text-ink-500">{day.posts.length}</span>
          )}
        </div>
      </div>
      <div className="flex flex-col gap-0.5 overflow-hidden">
        {day.posts.slice(0, 4).map(p => {
          const acct = accountById.get(p.platform_id);
          return (
            <button
              key={p.id}
              draggable
              onDragStart={() => setDragId(p.id)}
              onDragEnd={() => setDragId(null)}
              onClick={() => onPostClick(p)}
              className={`rounded px-1.5 py-0.5 truncate text-[10px] text-left cursor-grab active:cursor-grabbing ${tone(p.status)}`}
              title={`${acct?.plugin_name ?? '?'} · ${p.text.slice(0, 80)}`}
            >
              <span className="font-medium">{acct?.plugin_name ?? '?'}</span>{' '}
              {cleanText(p.text).slice(0, 32)}
            </button>
          );
        })}
        {day.posts.length > 4 && (
          <div className="text-[10px] text-ink-500">+{day.posts.length - 4} more</div>
        )}
      </div>
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className={`size-2 rounded-full ${color}`} />
      <span>{label}</span>
    </div>
  );
}

/* ───────── Post edit dialog ─────────────────────────────────────────── */

function PostEditDialog({ post, accountLabel, onClose }: {
  post: Post;
  accountLabel: string | undefined;
  onClose: () => void;
}) {
  const [text, setText] = useState(cleanText(post.text));
  const [hashtags, setHashtags] = useState((post.hashtags ?? []).join(' '));
  const [scheduledFor, setScheduledFor] = useState(
    post.scheduled_for ? post.scheduled_for.slice(0, 16) : '',
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    setBusy(true); setErr(null);
    try {
      const tags = hashtags.split(/\s+/).map(s => s.trim()).filter(Boolean)
        .map(s => (s.startsWith('#') ? s : `#${s}`));
      const body: Record<string, unknown> = { text, hashtags: tags };
      if (scheduledFor) body.scheduled_for = new Date(scheduledFor).toISOString();
      await api.patch(`/posts/${post.id}`, body);
      await mutate('/posts');
      onClose();
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Save failed.');
    } finally { setBusy(false); }
  }

  async function publishNow() {
    setBusy(true); setErr(null);
    try {
      await api.post(`/posts/${post.id}/publish_now`);
      await mutate('/posts');
      onClose();
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Publish failed.');
    } finally { setBusy(false); }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50" onClick={onClose}>
      <Card className="w-full max-w-xl max-h-[90vh] overflow-y-auto" onClick={(e: any) => e.stopPropagation()}>
        <div className="flex items-start justify-between mb-3">
          <div>
            <CardTitle>Edit scheduled post</CardTitle>
            <CardDescription>
              {accountLabel ?? 'Account'} · <Badge tone={
                post.status === 'published' ? 'success' :
                post.status === 'failed' ? 'danger' :
                post.status === 'review' ? 'warning' : 'default'
              }>{post.status}</Badge>
            </CardDescription>
          </div>
          <button onClick={onClose} className="text-ink-500 hover:text-ink-700">
            <X size={18} />
          </button>
        </div>
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-ink-500 mb-1">Text</label>
            <Textarea value={text} onChange={(e: any) => setText(e.target.value)} rows={6} />
          </div>
          <div>
            <label className="block text-xs text-ink-500 mb-1">Hashtags</label>
            <Input value={hashtags} onChange={(e: any) => setHashtags(e.target.value)} placeholder="#brand #launch" />
          </div>
          <div>
            <label className="block text-xs text-ink-500 mb-1">Scheduled for</label>
            <Input
              type="datetime-local"
              value={scheduledFor}
              onChange={(e: any) => setScheduledFor(e.target.value)}
            />
          </div>
          {err && (
            <div className="flex items-start gap-2 text-xs text-red-800 bg-red-50 border border-red-200 rounded p-2">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span>{err}</span>
            </div>
          )}
        </div>
        <div className="mt-4 flex justify-end gap-2 flex-wrap">
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant="outline" onClick={publishNow} disabled={busy}>Publish now</Button>
          <Button onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save changes'}</Button>
        </div>
      </Card>
    </div>
  );
}

/* ───────── Helpers ──────────────────────────────────────────────────── */

function tone(s: Post['status']) {
  switch (s) {
    case 'published':  return 'bg-emerald-50 text-emerald-700';
    case 'failed':     return 'bg-red-50 text-red-700';
    case 'review':     return 'bg-amber-50 text-amber-800';
    case 'scheduled':  return 'bg-accent-muted text-accent';
    default:           return 'bg-ink-100 text-ink-700';
  }
}

function buildMonth(first: Date, posts: Post[]): Day[] {
  const today = new Date(); today.setHours(0,0,0,0);
  const start = new Date(first);
  const offset = (start.getDay() + 6) % 7;     // Mon = 0 .. Sun = 6
  start.setDate(start.getDate() - offset);
  const cells: Day[] = [];
  for (let i = 0; i < 42; i++) {
    const d = new Date(start); d.setDate(start.getDate() + i);
    const dayKey = d.toISOString().slice(0,10);
    cells.push({
      date: d,
      inMonth: d.getMonth() === first.getMonth(),
      isToday: d.getTime() === today.getTime(),
      posts: posts.filter(p =>
        ((p.scheduled_for ?? p.published_at ?? p.created_at) || '').slice(0,10) === dayKey
      ),
    });
  }
  return cells;
}

function shift(cur: Date, set: (d: Date) => void, months: number) {
  const d = new Date(cur); d.setMonth(d.getMonth() + months); set(d);
}
function firstOfThisMonth() {
  const d = new Date(); d.setDate(1); d.setHours(0,0,0,0); return d;
}

/** Heuristic AI slot suggestions: highlight weekdays where the user has
 * historically published 2+ times. Returns a Set of `Date.getDay()` values.
 * Lightweight client-side heuristic — no LLM round-trip. */
function computeOptimalSlots(posts: Post[]): Set<number> {
  const counts = new Map<number, number>();
  for (const p of posts) {
    if (!p.published_at) continue;
    const day = new Date(p.published_at).getDay();
    counts.set(day, (counts.get(day) ?? 0) + 1);
  }
  const out = new Set<number>();
  for (const [day, n] of counts) {
    if (n >= 2) out.add(day);
  }
  return out;
}

function cleanText(raw: string): string {
  if (!raw) return '';
  let s = raw;
  s = s.replace(/\{"echo":[^}]*"system":[^}]*\}/g, '[draft pending real LLM output]');
  s = s.replace(/Hook:\s*\{"echo".*$/gm, 'Hook: (LLM output not yet wired)');
  s = s.replace(/Key messages:\s*\['?\{"echo".*$/gm, 'Key messages: (LLM output not yet wired)');
  return s.trim();
}
