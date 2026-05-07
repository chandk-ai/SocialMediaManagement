'use client';
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { useApi } from '@/lib/api/client';
import type { Workflow, Post, Trigger, Review } from '@/lib/api/types';
import { useMemo } from 'react';
import { formatDateTime } from '@/lib/utils';

type Event = { ts: string; actor: string; action: string; resource: string; tone?: string };

export default function AuditPage() {
  // Until we ship a dedicated audit_log endpoint, derive a meaningful
  // timeline from workflows / posts / triggers / reviews — they all carry
  // created_at + updated_at + status. The Supabase audit_log table will
  // replace this client-side aggregation when the backend route lands.
  const { data: workflows } = useApi<Workflow[]>('/workflows');
  const { data: posts }     = useApi<Post[]>('/posts');
  const { data: triggers }  = useApi<Trigger[]>('/triggers');
  const { data: reviews }   = useApi<Review[]>('/reviews');

  const events = useMemo<Event[]>(() => {
    const out: Event[] = [];
    for (const w of workflows ?? []) {
      out.push({ ts: w.created_at, actor: 'system', action: 'workflow.created', resource: w.name });
      if (w.status === 'active') out.push({ ts: w.updated_at, actor: 'system', action: 'workflow.activated', resource: w.name });
    }
    for (const t of triggers ?? []) {
      out.push({ ts: t.created_at, actor: 'system', action: 'trigger.created',
                 resource: `${t.plugin_name} → ${t.display_name}` });
    }
    for (const p of posts ?? []) {
      const action = `post.${p.status}`;
      const ts = p.published_at ?? p.created_at;
      out.push({ ts, actor: 'agent', action, resource: p.text.slice(0, 80),
                 tone: p.status === 'failed' ? 'danger' :
                       p.status === 'published' ? 'success' :
                       p.status === 'review' ? 'warning' : 'default' });
    }
    for (const r of reviews ?? []) {
      out.push({ ts: r.created_at, actor: r.recipient || 'in_app',
                 action: `review.${r.status}`,
                 resource: `${r.channel} · ${r.drafts_snapshot.length} draft(s)`,
                 tone: r.status === 'pending' ? 'warning' : 'info' });
    }
    return out.sort((a, b) => b.ts.localeCompare(a.ts));
  }, [workflows, posts, triggers, reviews]);

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Audit log" />
        <main className="flex-1 overflow-y-auto p-6">
          <Card>
            <CardTitle>Activity timeline</CardTitle>
            <CardDescription>Append-only log of state-changing events</CardDescription>
            <div className="mt-4 divide-y divide-ink-200">
              {events.length === 0 && (
                <p className="text-sm text-ink-500 py-4">No activity yet.</p>
              )}
              {events.slice(0, 200).map((e, i) => (
                <div key={i} className="py-3 grid grid-cols-12 gap-3 items-start">
                  <div className="col-span-2 text-xs text-ink-500 font-mono">
                    {formatDateTime(e.ts)}
                  </div>
                  <div className="col-span-2">
                    <Badge tone={(e.tone as any) ?? 'default'}>{e.action}</Badge>
                  </div>
                  <div className="col-span-2 text-xs text-ink-500 truncate">{e.actor}</div>
                  <div className="col-span-6 text-sm truncate">{e.resource}</div>
                </div>
              ))}
            </div>
            {events.length > 200 && (
              <p className="text-xs text-ink-500 mt-3">Showing the most recent 200 events.</p>
            )}
          </Card>
        </main>
      </div>
    </div>
  );
}
