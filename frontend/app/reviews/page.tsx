'use client';
/**
 * Reviews page lists drafts that need a human decision from two sources:
 *
 *   1. ReviewSession entities — created by the workflow agent loop when a
 *      review channel (in-app / WhatsApp / Telegram / Slack / email) was
 *      configured on the workflow. These have richer context (multi-platform
 *      drafts grouped, channel + recipient) and accept approve/revise/reject.
 *
 *   2. Plain Post rows whose status is "review" but no session was opened
 *      (typical for the default "no review channel" workflows). These are
 *      shown as a fallback so nothing slips through the cracks. Approve here
 *      transitions Post → approved → publishing queue.
 *
 * The two are deduped: any Post already covered by an active ReviewSession
 * (matched by run_id or post id) is hidden from the Posts list.
 */
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Input';
import { EmptyState } from '@/components/ui/EmptyState';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { useApi, api, ApiError } from '@/lib/api/client';
import type { Review, Post } from '@/lib/api/types';
import { MessageSquare, Check, X, Edit3, AlertTriangle, Send } from 'lucide-react';
import { useState } from 'react';
import { mutate } from 'swr';
import { formatDateTime } from '@/lib/utils';

export default function ReviewsPage() {
  const { data: reviews } = useApi<Review[]>('/reviews');
  const { data: reviewPosts } = useApi<Post[]>('/posts?status=review');

  // Dedupe: if a Post's run_id matches a session's run_id, the session covers it.
  const coveredRuns = new Set((reviews ?? []).map(r => r.run_id));
  const orphanPosts = (reviewPosts ?? []).filter(p => !coveredRuns.has(p.run_id));
  const total = (reviews?.length ?? 0) + orphanPosts.length;

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Reviews" />
        <main className="flex-1 overflow-y-auto p-6">
          <p className="text-xs text-ink-500 mb-3">
            Drafts waiting for a human decision. Reviews initiated via WhatsApp / Telegram / Slack are
            also resolvable in those channels — anything you decide here is mirrored back to the agent.
          </p>

          {total === 0 ? (
            <EmptyState
              icon={<MessageSquare size={32} />}
              title="Nothing to review"
              description="Workflows that need approval will land here."
            />
          ) : (
            <div className="space-y-4">
              {(reviews ?? []).map(r => <ReviewCard key={r.id} review={r} />)}
              {orphanPosts.map(p => <PostReviewCard key={p.id} post={p} />)}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

/* ───────── ReviewSession (channel-based) card ───────────────────────── */

function ReviewCard({ review }: { review: Review }) {
  const [feedback, setFeedback] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function decide(kind: 'approve' | 'revise' | 'reject') {
    setBusy(kind); setErr(null);
    try {
      await api.post(`/reviews/${review.id}/decision`, {
        kind, feedback: kind === 'revise' ? feedback : '',
      });
      await mutate('/reviews');
      await mutate('/posts?status=review');
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Decision failed.');
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <div className="flex items-start justify-between">
        <div>
          <CardTitle>Awaiting your decision</CardTitle>
          <CardDescription>
            via {review.channel} · to {review.recipient || '(in-app)'}
          </CardDescription>
        </div>
        <Badge tone="warning">{review.status}</Badge>
      </div>

      <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3">
        {review.drafts_snapshot.map((d, i) => (
          <div key={i} className="rounded-xl border border-ink-200 p-3">
            <div className="text-xs text-ink-500 mb-1">{d.platform_name}</div>
            <div className="text-sm whitespace-pre-line break-words">{cleanText(d.text)}</div>
            {d.hashtags?.length ? (
              <div className="text-xs text-accent mt-2">{d.hashtags.join(' ')}</div>
            ) : null}
          </div>
        ))}
      </div>

      <div className="mt-4">
        <label className="block text-xs text-ink-500 mb-1">
          Feedback (only used for "Revise")
        </label>
        <Textarea
          value={feedback}
          onChange={e => setFeedback(e.target.value)}
          placeholder='e.g. "Make it more casual; cut to 2 sentences"'
        />
      </div>

      {err && (
        <div className="mt-3 flex items-start gap-2 text-xs text-red-800 bg-red-50 border border-red-200 rounded p-2">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span>{err}</span>
        </div>
      )}

      <div className="mt-3 flex gap-2 justify-end flex-wrap">
        <Button variant="outline" disabled={busy !== null} onClick={() => decide('reject')}>
          <X size={14} /> {busy === 'reject' ? 'Rejecting…' : 'Reject'}
        </Button>
        <Button
          variant="outline"
          disabled={busy !== null || !feedback}
          onClick={() => decide('revise')}
        >
          <Edit3 size={14} /> {busy === 'revise' ? 'Sending…' : 'Revise'}
        </Button>
        <Button disabled={busy !== null} onClick={() => decide('approve')}>
          <Check size={14} /> {busy === 'approve' ? 'Approving…' : 'Approve & Post'}
        </Button>
      </div>
    </Card>
  );
}

/* ───────── Plain review-status Post card (no session) ──────────────── */

function PostReviewCard({ post }: { post: Post }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function call(action: string, fn: () => Promise<unknown>) {
    setBusy(action); setErr(null);
    try {
      await fn();
      await mutate('/reviews');
      await mutate('/posts?status=review');
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Action failed.');
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <div className="flex items-start justify-between">
        <div>
          <CardTitle>Draft post</CardTitle>
          <CardDescription>
            no review channel · created {formatDateTime(post.created_at)}
          </CardDescription>
        </div>
        <Badge tone="warning">review</Badge>
      </div>

      <div className="mt-4">
        <p className="text-sm whitespace-pre-line break-words">{cleanText(post.text)}</p>
        {post.hashtags.length > 0 && (
          <div className="mt-2 text-xs text-accent">{post.hashtags.join(' ')}</div>
        )}
      </div>

      {err && (
        <div className="mt-3 flex items-start gap-2 text-xs text-red-800 bg-red-50 border border-red-200 rounded p-2">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span>{err}</span>
        </div>
      )}

      <div className="mt-3 flex gap-2 justify-end flex-wrap">
        <Button
          variant="outline"
          disabled={busy !== null}
          onClick={() =>
            confirm('Reject and delete this draft?') &&
            call('reject', () => api.del(`/posts/${post.id}`))
          }
        >
          <X size={14} /> Reject
        </Button>
        <Button
          variant="outline"
          disabled={busy !== null}
          onClick={() => call('approve', () => api.post(`/posts/${post.id}/approve`))}
        >
          <Check size={14} /> {busy === 'approve' ? 'Approving…' : 'Approve'}
        </Button>
        <Button
          disabled={busy !== null}
          onClick={() => call('publish', () => api.post(`/posts/${post.id}/publish_now`))}
        >
          <Send size={14} /> {busy === 'publish' ? 'Publishing…' : 'Approve & publish'}
        </Button>
      </div>
    </Card>
  );
}

/* ───────── helper ──────────────────────────────────────────────────── */

function cleanText(raw: string): string {
  if (!raw) return '';
  let s = raw;
  s = s.replace(/\{"echo":[^}]*"system":[^}]*\}/g, '[draft pending real LLM output]');
  s = s.replace(/Hook:\s*\{"echo".*$/gm, 'Hook: (LLM output not yet wired)');
  s = s.replace(/Key messages:\s*\['?\{"echo".*$/gm, 'Key messages: (LLM output not yet wired)');
  return s.trim();
}
