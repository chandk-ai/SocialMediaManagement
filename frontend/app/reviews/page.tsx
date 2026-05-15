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
import { PlatformIcon, type PlatformName } from '@/components/ui/PlatformIcon';
import { useApi, api, ApiError } from '@/lib/api/client';
import type { Review, ReviewDraftSnapshot, Post } from '@/lib/api/types';
import { MessageSquare, Check, X, Edit3, AlertTriangle, Send } from 'lucide-react';
import { useState, useMemo } from 'react';
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
  // Per-target inclusion state — keyed by platform_id. Defaults to
  // "include everything except the ones the backend already has as
  // excluded" so a page refresh preserves a prior partial selection.
  // Entries without a platform_id (legacy snapshots pre-enrichment)
  // can't be excluded individually — they just publish as a group.
  const initiallyExcluded = useMemo(
    () => new Set(review.excluded_platform_ids ?? []),
    [review.excluded_platform_ids],
  );
  const [excluded, setExcluded] = useState<Set<string>>(initiallyExcluded);

  function toggleTarget(platformId?: string) {
    if (!platformId) return;     // legacy snapshot — no granular control
    setExcluded(prev => {
      const next = new Set(prev);
      next.has(platformId) ? next.delete(platformId) : next.add(platformId);
      return next;
    });
  }

  const targetableCount = review.drafts_snapshot.filter(d => d.platform_id).length;
  const includedCount = review.drafts_snapshot.filter(
    d => d.platform_id && !excluded.has(d.platform_id),
  ).length;
  // Block "Approve" when the reviewer has excluded ALL targets — that
  // would be a no-op publish with no auditable intent. They should
  // use Reject instead.
  const allExcluded = targetableCount > 0 && includedCount === 0;

  async function decide(kind: 'approve' | 'revise' | 'reject') {
    setBusy(kind); setErr(null);
    try {
      await api.post(`/reviews/${review.id}/decision`, {
        kind,
        feedback: kind === 'revise' ? feedback : '',
        // Always include — backend persists this on the session so
        // the choice survives a revision round. On reject/revise the
        // value is informational; on approve it gates the publish.
        excluded_platform_ids: Array.from(excluded),
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

      {/* Target summary line: how many accounts will receive the post,
          out of the fan-out total. Updates live as the reviewer toggles. */}
      {targetableCount > 0 && (
        <div className="mt-3 text-xs text-ink-600">
          Publishing to <span className="font-medium">{includedCount}</span> of{' '}
          <span className="font-medium">{targetableCount}</span> target accounts.
          Untick a card to skip that account.
        </div>
      )}

      <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-3">
        {review.drafts_snapshot.map((d, i) => (
          <DraftTile
            key={d.post_id ?? `${d.platform_name}-${i}`}
            draft={d}
            excluded={d.platform_id ? excluded.has(d.platform_id) : false}
            onToggle={() => toggleTarget(d.platform_id)}
          />
        ))}
      </div>

      <div className="mt-4">
        <label className="block text-xs text-ink-500 mb-1">
          Feedback (used for "Revise" to regenerate, or for the audit trail on Reject)
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
          title={!feedback ? 'Enter feedback above to enable Revise' : 'Send back to the agent with feedback'}
        >
          <Edit3 size={14} /> {busy === 'revise' ? 'Sending…' : 'Revise with feedback'}
        </Button>
        <Button
          disabled={busy !== null || allExcluded}
          onClick={() => decide('approve')}
          title={allExcluded ? 'You\'ve excluded every target — Reject instead' : 'Approve and publish to selected targets'}
        >
          <Check size={14} />{' '}
          {busy === 'approve'
            ? 'Approving…'
            : targetableCount > 0
              ? `Approve & Post to ${includedCount}`
              : 'Approve & Post'}
        </Button>
      </div>
    </Card>
  );
}

/* ───────── Per-target draft tile with include/exclude toggle ─────────── */

function DraftTile({
  draft,
  excluded,
  onToggle,
}: {
  draft: ReviewDraftSnapshot;
  excluded: boolean;
  onToggle: () => void;
}) {
  const pluginName = draft.plugin_name ?? draft.platform_name;
  const displayName = draft.display_name ?? draft.platform_name;
  const canToggle = !!draft.platform_id;

  return (
    <label
      className={
        'rounded-xl border p-3 transition flex flex-col gap-2 ' +
        (canToggle ? 'cursor-pointer ' : '') +
        (excluded
          ? 'border-ink-200 bg-ink-50 opacity-60'
          : 'border-ink-200 hover:border-accent/40 bg-bg-card')
      }
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <PlatformIcon name={pluginName as PlatformName} size={18} />
          <div className="min-w-0">
            <div className="text-sm font-medium truncate">{displayName}</div>
            {draft.account_handle && (
              <div className="text-[11px] text-ink-500 truncate">{draft.account_handle}</div>
            )}
          </div>
        </div>
        {canToggle && (
          <input
            type="checkbox"
            className="h-4 w-4 accent-accent shrink-0"
            checked={!excluded}
            onChange={onToggle}
            aria-label={excluded ? 'Include this target' : 'Skip this target'}
            onClick={e => e.stopPropagation()}
          />
        )}
      </div>
      <div className="text-sm whitespace-pre-line break-words">{cleanText(draft.text)}</div>
      {draft.hashtags?.length ? (
        <div className="text-xs text-accent">{draft.hashtags.join(' ')}</div>
      ) : null}
      {draft.media?.length ? (
        <div className="flex gap-1 flex-wrap">
          {draft.media.slice(0, 3).map((m, mi) =>
            m.kind === 'video' ? (
              <span
                key={mi}
                className="text-[10px] uppercase tracking-wider px-2 py-1 rounded bg-ink-100 text-ink-600"
              >
                video
              </span>
            ) : (
              <img
                key={mi}
                src={m.url}
                alt={m.alt_text ?? ''}
                className="h-12 w-12 rounded object-cover"
              />
            ),
          )}
        </div>
      ) : null}
      {excluded && (
        <div className="text-[11px] font-medium text-ink-500 uppercase tracking-wider">
          Skipping this account
        </div>
      )}
    </label>
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

      {/* Target chip — same look as the per-target tiles on session-based
          reviews so the reviewer always knows where this is going. */}
      {post.platform_plugin_name && (
        <div className="mt-3 inline-flex items-center gap-2 rounded-md border border-ink-200 px-2 py-1 text-xs">
          <PlatformIcon name={post.platform_plugin_name as PlatformName} size={14} />
          <span className="font-medium">{post.platform_display_name ?? post.platform_plugin_name}</span>
          {post.account_handle && <span className="text-ink-500">· {post.account_handle}</span>}
        </div>
      )}

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
