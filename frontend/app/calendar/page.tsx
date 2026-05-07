'use client';
import { Card, CardTitle } from '@/components/ui/Card';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { useApi } from '@/lib/api/client';
import type { Post, Platform } from '@/lib/api/types';
import { useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/Button';

export default function CalendarPage() {
  const { data: posts } = useApi<Post[]>('/posts');
  const { data: platforms } = useApi<Platform[]>('/platforms');
  const [cursor, setCursor] = useState(() => {
    const d = new Date(); d.setDate(1); d.setHours(0, 0, 0, 0); return d;
  });

  const platformLookup = useMemo(
    () => new Map((platforms ?? []).map(p => [p.id, p])),
    [platforms],
  );
  const month = useMemo(() => buildMonth(cursor, posts ?? []), [cursor, posts]);

  const monthLabel = cursor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Content calendar" />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-base font-semibold tracking-tight">{monthLabel}</h2>
            <div className="flex gap-2">
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

          <Card className="p-3">
            <div className="grid grid-cols-7 gap-1 text-xs text-ink-500 mb-1">
              {['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map(d => (
                <div key={d} className="px-2 py-1">{d}</div>
              ))}
            </div>
            <div className="grid grid-cols-7 gap-1">
              {month.map((day, i) => (
                <div
                  key={i}
                  className={`min-h-[110px] rounded-lg border p-1.5 text-xs flex flex-col gap-1 ${
                    day.inMonth ? 'border-ink-200 bg-white' : 'border-ink-200/50 bg-ink-50 text-ink-300'
                  } ${day.isToday ? 'ring-2 ring-accent' : ''}`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium">{day.date.getDate()}</span>
                    {day.posts.length > 0 && (
                      <span className="text-[10px] text-ink-500">{day.posts.length}</span>
                    )}
                  </div>
                  <div className="flex flex-col gap-0.5 overflow-hidden">
                    {day.posts.slice(0, 3).map(p => {
                      const plat = platformLookup.get(p.platform_id);
                      return (
                        <div key={p.id} className={`rounded px-1 py-0.5 truncate text-[10px] ${tone(p.status)}`}>
                          <span className="font-medium">{plat?.plugin_name ?? '?'}</span> {p.text.slice(0, 40)}
                        </div>
                      );
                    })}
                    {day.posts.length > 3 && (
                      <div className="text-[10px] text-ink-500">+{day.posts.length - 3} more</div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </main>
      </div>
    </div>
  );
}

function tone(s: Post['status']) {
  switch (s) {
    case 'published':  return 'bg-emerald-50 text-emerald-700';
    case 'failed':     return 'bg-red-50 text-red-700';
    case 'review':     return 'bg-amber-50 text-amber-800';
    case 'scheduled':  return 'bg-accent-muted text-accent';
    default:           return 'bg-ink-100 text-ink-700';
  }
}

function buildMonth(first: Date, posts: Post[]) {
  const today = new Date(); today.setHours(0,0,0,0);
  // Find Monday at-or-before first.
  const start = new Date(first);
  const offset = (start.getDay() + 6) % 7;     // Mon = 0 .. Sun = 6
  start.setDate(start.getDate() - offset);
  const cells: { date: Date; inMonth: boolean; isToday: boolean; posts: Post[] }[] = [];
  for (let i = 0; i < 42; i++) {
    const d = new Date(start); d.setDate(start.getDate() + i);
    const dayKey = d.toISOString().slice(0,10);
    cells.push({
      date: d,
      inMonth: d.getMonth() === first.getMonth(),
      isToday: d.getTime() === today.getTime(),
      posts: posts.filter(p =>
        (p.scheduled_for ?? p.published_at ?? p.created_at).slice(0,10) === dayKey
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
