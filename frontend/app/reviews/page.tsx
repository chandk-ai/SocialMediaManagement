'use client';
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Input';
import { EmptyState } from '@/components/ui/EmptyState';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { useApi, api } from '@/lib/api/client';
import type { Review } from '@/lib/api/types';
import { MessageSquareCheck, Check, X, Edit3 } from 'lucide-react';
import { useState } from 'react';
import { mutate } from 'swr';

export default function ReviewsPage() {
  const { data: reviews } = useApi<Review[]>('/reviews');

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Reviews" />
        <main className="flex-1 overflow-y-auto p-6">
          <p className="text-xs text-ink-500 mb-3">
            Drafts waiting for a human decision. Reviews initiated via WhatsApp / Instagram are
            also resolved by replying in those channels — anything you decide here is mirrored back to the agent.
          </p>
          {reviews && reviews.length === 0 ? (
            <EmptyState
              icon={<MessageSquareCheck size={32} />}
              title="Nothing to review"
              description="Workflows that need approval will land here."
            />
          ) : (
            <div className="space-y-4">
              {(reviews ?? []).map(r => <ReviewCard key={r.id} review={r} />)}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

function ReviewCard({ review }: { review: Review }) {
  const [feedback, setFeedback] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  async function decide(kind: 'approve' | 'revise' | 'reject') {
    setBusy(kind);
    try {
      await api.post(`/reviews/${review.id}/decision`, {
        kind, feedback: kind === 'revise' ? feedback : '',
      });
      await mutate('/reviews');
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
            <div className="text-sm whitespace-pre-line">{d.text}</div>
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

      <div className="mt-3 flex gap-2 justify-end">
        <Button variant="outline" disabled={busy !== null} onClick={() => decide('reject')}>
          <X size={14} /> Reject
        </Button>
        <Button variant="outline" disabled={busy !== null || !feedback}
                onClick={() => decide('revise')}>
          <Edit3 size={14} /> Revise
        </Button>
        <Button disabled={busy !== null} onClick={() => decide('approve')}>
          <Check size={14} /> Approve & Post
        </Button>
      </div>
    </Card>
  );
}
