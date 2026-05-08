'use client';
/**
 * Audit log page — reads the real `smms.audit_log` table via /audit-logs.
 *
 * Falls back to client-side aggregation across workflows/posts/triggers/
 * reviews when the audit endpoint is unavailable (e.g. memory backend in
 * dev). Once the real table has events, those take over.
 */
import { useMemo, useState } from 'react';
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { Input } from '@/components/ui/Input';
import { useApi } from '@/lib/api/client';
import type { Workflow, Post, Trigger, Review } from '@/lib/api/types';
import { formatDateTime } from '@/lib/utils';

type AuditEvent = {
  id: string;
  occurred_at: string;
  actor_type: string;
  actor_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  request_id: string | null;
};

type AuditResponse = { events: AuditEvent[] };

type Row = {
  ts: string;
  actor: string;
  action: string;
  resource: string;
  tone?: 'success' | 'danger' | 'warning' | 'info' | 'default';
};

const ACTION_TONES: Record<string, Row['tone']> = {
  '.fail': 'danger',
  '.delete': 'danger',
  '.remove': 'danger',
  '.publish': 'success',
  '.connect': 'success',
  '.activate': 'success',
  '.pause': 'warning',
  '.set': 'info',
  '.create': 'info',
  '.run': 'info',
};

function toneFor(action: string): Row['tone'] {
  for (const [suffix, tone] of Object.entries(ACTION_TONES)) {
    if (action.endsWith(suffix)) return tone;
  }
  return 'default';
}

export default function AuditPage() {
  const [filter, setFilter] = useState('');

  // Real audit log first.
  const { data: audit, error: auditErr } = useApi<AuditResponse>('/audit-logs?limit=500&days=60');

  // Synthetic fallback — used only when the real audit endpoint isn't
  // available (503 in memory mode, network error, or empty result on a
  // brand-new install).
  const fallbackOk = !audit || (audit.events ?? []).length === 0;
  const { data: workflows } = useApi<Workflow[]>(fallbackOk ? '/workflows' : null);
  const { data: posts }     = useApi<Post[]>(fallbackOk ? '/posts' : null);
  const { data: triggers }  = useApi<Trigger[]>(fallbackOk ? '/triggers' : null);
  const { data: reviews }   = useApi<Review[]>(fallbackOk ? '/reviews' : null);

  const rows = useMemo<Row[]>(() => {
    const out: Row[] = [];
    if (audit && (audit.events ?? []).length > 0) {
      for (const e of audit.events) {
        out.push({
          ts: e.occurred_at,
          actor: e.actor_type + (e.actor_id ? ` · ${e.actor_id.slice(0, 8)}` : ''),
          action: e.action,
          resource: describe(e),
          tone: toneFor(e.action),
        });
      }
    } else {
      // Fallback synthesis (legacy behaviour preserved so the page is never
      // empty in dev / memory mode).
      for (const w of workflows ?? []) {
        out.push({ ts: w.created_at, actor: 'system', action: 'workflow.create', resource: w.name });
        if (w.status === 'active') out.push({ ts: w.updated_at, actor: 'system', action: 'workflow.activate', resource: w.name, tone: 'success' });
      }
      for (const t of triggers ?? []) {
        out.push({ ts: t.created_at, actor: 'system', action: 'trigger.create',
                   resource: `${t.plugin_name} → ${t.display_name}`, tone: 'info' });
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
    }
    return out.sort((a, b) => b.ts.localeCompare(a.ts));
  }, [audit, workflows, posts, triggers, reviews]);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(r =>
      r.action.toLowerCase().includes(q) ||
      r.actor.toLowerCase().includes(q) ||
      r.resource.toLowerCase().includes(q),
    );
  }, [rows, filter]);

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Audit log" />
        <main className="flex-1 overflow-y-auto p-6">
          <Card>
            <div className="flex items-start justify-between gap-3 flex-wrap">
              <div>
                <CardTitle>Activity timeline</CardTitle>
                <CardDescription>
                  Append-only log of state-changing events.
                  {auditErr && (
                    <span className="block text-amber-700 mt-1">
                      Live audit log unavailable — showing synthesized events.
                    </span>
                  )}
                </CardDescription>
              </div>
              <Input
                placeholder="Filter by action, actor, or text…"
                value={filter}
                onChange={(e: any) => setFilter(e.target.value)}
                className="w-64"
              />
            </div>
            <div className="mt-4 divide-y divide-ink-200">
              {filtered.length === 0 && (
                <p className="text-sm text-ink-500 py-4">No activity yet.</p>
              )}
              {filtered.slice(0, 500).map((e, i) => (
                <div key={i} className="py-3 grid grid-cols-12 gap-3 items-start">
                  <div className="col-span-2 text-xs text-ink-500 font-mono">
                    {formatDateTime(e.ts)}
                  </div>
                  <div className="col-span-2">
                    <Badge tone={e.tone ?? 'default'}>{e.action}</Badge>
                  </div>
                  <div className="col-span-2 text-xs text-ink-500 truncate">{e.actor}</div>
                  <div className="col-span-6 text-sm truncate">{e.resource}</div>
                </div>
              ))}
            </div>
            {filtered.length > 500 && (
              <p className="text-xs text-ink-500 mt-3">Showing the most recent 500 events.</p>
            )}
          </Card>
        </main>
      </div>
    </div>
  );
}

function describe(e: AuditEvent): string {
  const detail = (e.after || e.before || {}) as Record<string, unknown>;
  const bits: string[] = [];
  if (typeof detail.name === 'string') bits.push(detail.name);
  if (typeof detail.display_name === 'string') bits.push(detail.display_name);
  if (typeof detail.plugin_name === 'string') bits.push(detail.plugin_name);
  if (typeof detail.account_handle === 'string') bits.push(`@${detail.account_handle}`);
  if (typeof detail.provider === 'string') bits.push(detail.provider);
  if (typeof detail.model === 'string') bits.push(detail.model);
  if (typeof detail.last_4 === 'string') bits.push(`····${detail.last_4}`);
  if (typeof detail.status === 'string') bits.push(`(${detail.status})`);
  if (typeof detail.reason === 'string') bits.push(`— ${detail.reason}`);
  if (bits.length === 0 && e.resource_id) bits.push(e.resource_id.slice(0, 8));
  return `${e.resource_type}: ${bits.join(' · ') || e.resource_id || ''}`.trim();
}
