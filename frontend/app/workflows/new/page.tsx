'use client';
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Input, Textarea } from '@/components/ui/Input';
import { Badge } from '@/components/ui/Badge';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { useApi, api } from '@/lib/api/client';
import type { Platform, Source, PluginInfo } from '@/lib/api/types';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowLeft } from 'lucide-react';
import Link from 'next/link';

export default function NewWorkflowPage() {
  const router = useRouter();
  const { data: sources }   = useApi<Source[]>('/sources');
  const { data: platforms } = useApi<Platform[]>('/platforms');
  const { data: llms }      = useApi<PluginInfo[]>('/plugins?kind=llm');

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [pickedSources, setPickedSources] = useState<string[]>([]);
  const [pickedPlatforms, setPickedPlatforms] = useState<string[]>([]);
  const [tone, setTone] = useState('professional');
  const [audience, setAudience] = useState('general');
  const [llm, setLlm] = useState('mock');
  const [scheduleKind, setScheduleKind] = useState('manual');
  const [cron, setCron] = useState('');

  function toggle(arr: string[], setArr: (v: string[]) => void, id: string) {
    setArr(arr.includes(id) ? arr.filter(x => x !== id) : [...arr, id]);
  }

  async function submit() {
    await api.post('/workflows', {
      name, description,
      source_ids: pickedSources,
      platform_ids: pickedPlatforms,
      config: { tone, audience, llm_provider: llm },
      schedule: { kind: scheduleKind, cron: cron || null, timezone: 'UTC' },
    });
    router.push('/workflows');
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="New workflow" />
        <main className="flex-1 overflow-y-auto p-6">
          <Link href="/workflows" className="inline-flex items-center gap-1 text-sm text-ink-500 hover:text-ink-700 mb-4">
            <ArrowLeft size={14} /> Back to workflows
          </Link>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <Card className="lg:col-span-2">
              <CardTitle>Basics</CardTitle>
              <CardDescription>Name, audience, and tone</CardDescription>
              <div className="mt-4 space-y-3">
                <Input placeholder="Name" value={name} onChange={e => setName(e.target.value)} />
                <Textarea placeholder="Description" value={description} onChange={e => setDescription(e.target.value)} />
                <div className="grid grid-cols-2 gap-3">
                  <Input placeholder="Tone (e.g. professional)" value={tone} onChange={e => setTone(e.target.value)} />
                  <Input placeholder="Audience (e.g. CTOs)"     value={audience} onChange={e => setAudience(e.target.value)} />
                </div>
                <div>
                  <label className="block text-xs text-ink-500 mb-1">LLM provider</label>
                  <select className="input" value={llm} onChange={e => setLlm(e.target.value)}>
                    {(llms ?? []).map(l => (
                      <option key={l.name} value={l.name}>{l.display_name}</option>
                    ))}
                  </select>
                </div>
              </div>
            </Card>

            <Card>
              <CardTitle>Schedule</CardTitle>
              <div className="mt-4 space-y-3">
                <select className="input" value={scheduleKind} onChange={e => setScheduleKind(e.target.value)}>
                  <option value="manual">Manual (run on demand)</option>
                  <option value="cron">Cron</option>
                  <option value="interval">Interval (minutes)</option>
                </select>
                {scheduleKind === 'cron' && (
                  <Input placeholder="0 9 * * MON-FRI" value={cron} onChange={e => setCron(e.target.value)} />
                )}
              </div>
            </Card>

            <Card className="lg:col-span-2">
              <CardTitle>Sources</CardTitle>
              <CardDescription>Choose where the agents pull reference content from</CardDescription>
              <div className="mt-4 flex flex-wrap gap-2">
                {(sources ?? []).map(s => (
                  <button
                    key={s.id} type="button"
                    onClick={() => toggle(pickedSources, setPickedSources, s.id)}
                    className={`badge cursor-pointer ${pickedSources.includes(s.id) ? 'bg-accent-muted text-accent' : 'bg-ink-100'}`}
                  >
                    {s.display_name}
                  </button>
                ))}
              </div>
            </Card>

            <Card>
              <CardTitle>Target accounts</CardTitle>
              <CardDescription>Pick one or more accounts (across any platforms)</CardDescription>
              <div className="mt-4 flex flex-wrap gap-2">
                {(platforms ?? []).map(p => (
                  <button
                    key={p.id} type="button"
                    onClick={() => toggle(pickedPlatforms, setPickedPlatforms, p.id)}
                    className={`badge cursor-pointer ${pickedPlatforms.includes(p.id) ? 'bg-accent-muted text-accent' : 'bg-ink-100'}`}
                    title={p.account_handle ?? ''}
                  >
                    <span className="font-medium">{p.plugin_name}</span>
                    <span className="opacity-60 ml-1">{p.display_name}</span>
                    {p.account_handle && <span className="opacity-50 ml-1">({p.account_handle})</span>}
                  </button>
                ))}
              </div>
            </Card>

            <div className="lg:col-span-3 flex justify-end gap-2">
              <Link href="/workflows"><Button variant="ghost">Cancel</Button></Link>
              <Button onClick={submit} disabled={!name || !pickedSources.length || !pickedPlatforms.length}>
                Create workflow
              </Button>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
