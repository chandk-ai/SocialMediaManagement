'use client';
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { EmptyState } from '@/components/ui/EmptyState';
import { useApi } from '@/lib/api/client';
import type { Post } from '@/lib/api/types';
import { FileText } from 'lucide-react';
import { useState } from 'react';
import { formatDateTime } from '@/lib/utils';

const FILTERS = ['all', 'review', 'approved', 'published', 'failed'] as const;

export default function PostsPage() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>('all');
  const { data } = useApi<Post[]>(filter === 'all' ? '/posts' : `/posts?status=${filter}`);

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Posts" />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="mb-4 flex items-center gap-2">
            {FILTERS.map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`badge ${filter === f ? 'bg-accent-muted text-accent' : 'bg-ink-100 text-ink-700'}`}
              >
                {f}
              </button>
            ))}
          </div>

          {data && data.length === 0 ? (
            <EmptyState
              icon={<FileText size={32} />}
              title="No posts in this view"
              description="Run a workflow to generate posts."
            />
          ) : (
            <div className="space-y-3">
              {(data ?? []).map(p => (
                <Card key={p.id}>
                  <div className="flex items-start gap-4">
                    <Badge tone={
                      p.status === 'published' ? 'success' :
                      p.status === 'failed'    ? 'danger'  :
                      p.status === 'review'    ? 'warning' : 'default'
                    }>{p.status}</Badge>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm text-ink-900 whitespace-pre-line">{p.text}</p>
                      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink-500">
                        <span>{p.hashtags.join(' ')}</span>
                        <span>·</span>
                        <span>{formatDateTime(p.created_at)}</span>
                        {p.error && <span className="text-red-600">· {p.error}</span>}
                      </div>
                    </div>
                  </div>
                </Card>
              ))}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
