'use client';
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { useApi, api } from '@/lib/api/client';
import type { Workflow } from '@/lib/api/types';
import { Workflow as WorkflowIcon, Play } from 'lucide-react';
import Link from 'next/link';
import { mutate } from 'swr';

export default function WorkflowsPage() {
  const { data } = useApi<Workflow[]>('/workflows');

  async function run(id: string) {
    await api.post(`/workflows/${id}/run`);
    await mutate('/workflows');
  }
  async function activate(id: string) {
    await api.post(`/workflows/${id}/activate`);
    await mutate('/workflows');
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Workflows" />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-ink-700">All workflows</h2>
            <Link href="/workflows/new"><Button size="sm">New workflow</Button></Link>
          </div>
          {data && data.length === 0 ? (
            <EmptyState
              icon={<WorkflowIcon size={32} />}
              title="Build your first workflow"
              description="Pick sources, target platforms, set the tone, and let the agents handle the rest."
              action={<Link href="/workflows/new"><Button>Create workflow</Button></Link>}
            />
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {(data ?? []).map(w => (
                <Card key={w.id}>
                  <div className="flex items-start justify-between">
                    <div className="min-w-0">
                      <CardTitle className="truncate">{w.name}</CardTitle>
                      <CardDescription className="truncate">{w.description || '—'}</CardDescription>
                    </div>
                    <Badge tone={w.status === 'active' ? 'success' : 'default'}>{w.status}</Badge>
                  </div>
                  <div className="mt-3 text-xs text-ink-500">
                    {w.source_ids.length} source{w.source_ids.length !== 1 && 's'} →{' '}
                    {w.platform_ids.length} platform{w.platform_ids.length !== 1 && 's'} ·{' '}
                    {w.schedule.kind}
                  </div>
                  <div className="mt-4 flex gap-2">
                    <Button size="sm" onClick={() => run(w.id)}>
                      <Play size={14} /> Run now
                    </Button>
                    {w.status !== 'active' && (
                      <Button variant="outline" size="sm" onClick={() => activate(w.id)}>Activate</Button>
                    )}
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
