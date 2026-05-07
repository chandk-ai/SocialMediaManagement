'use client';
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { EmptyState } from '@/components/ui/EmptyState';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { useApi, api } from '@/lib/api/client';
import { ApiErrorBanner } from '@/components/ui/ApiErrorBanner';
import type { PluginInfo, Platform, PlatformGroup } from '@/lib/api/types';
import { Plug, Plus, Star, X } from 'lucide-react';
import { useState } from 'react';
import { mutate } from 'swr';

export default function PlatformsPage() {
  const { data: plugins, error: pluginsErr, mutate: retryPlugins } =
    useApi<PluginInfo[]>('/plugins?kind=platform');
  const { data: groups, error: groupsErr, mutate: retryGroups } =
    useApi<PlatformGroup[]>('/platforms/grouped');
  const [adding, setAdding] = useState<PluginInfo | null>(null);
  const [label, setLabel] = useState('');
  const [handle, setHandle] = useState('');

  async function add() {
    if (!adding) return;
    await api.post('/platforms', {
      plugin_name: adding.name,
      display_name: label || `${adding.display_name} account`,
      account_handle: handle || null,
      config: {},
    });
    setAdding(null); setLabel(''); setHandle('');
    await mutate('/platforms/grouped');
  }

  const connectedPlugins = new Set((groups ?? []).map(g => g.plugin_name));

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Platforms" />
        <main className="flex-1 overflow-y-auto p-6 space-y-8">
          <ApiErrorBanner error={pluginsErr || groupsErr}
                          retry={() => { retryPlugins(); retryGroups(); }} />

          {/* Connected accounts, grouped by plugin */}
          <section>
            <div className="flex items-end justify-between mb-3">
              <div>
                <h2 className="text-sm font-semibold text-ink-700">Connected accounts</h2>
                <p className="text-xs text-ink-500">You can connect multiple accounts per platform.</p>
              </div>
            </div>
            {groups && groups.length === 0 ? (
              <EmptyState
                icon={<Plug size={32} />}
                title="No accounts connected"
                description="Add your first social media account below."
              />
            ) : (
              <div className="space-y-4">
                {(groups ?? []).map(g => (
                  <Card key={g.plugin_name}>
                    <div className="flex items-center justify-between mb-3">
                      <div>
                        <CardTitle>{g.display_name}</CardTitle>
                        <CardDescription>
                          {g.accounts.length} account{g.accounts.length !== 1 && 's'}
                        </CardDescription>
                      </div>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          const plug = (plugins ?? []).find(p => p.name === g.plugin_name);
                          if (plug) setAdding(plug);
                        }}
                      >
                        <Plus size={14} /> Add another account
                      </Button>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                      {g.accounts.map(a => (
                        <AccountTile key={a.id} account={a} />
                      ))}
                    </div>
                  </Card>
                ))}
              </div>
            )}
          </section>

          {/* Available plugins (only those not yet connected — but you can still add more accounts) */}
          <section>
            <h2 className="text-sm font-semibold text-ink-700 mb-3">Available platforms</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {(plugins ?? []).map(p => (
                <Card key={p.name}>
                  <div className="flex items-start justify-between">
                    <div>
                      <CardTitle>{p.display_name}</CardTitle>
                      <CardDescription>
                        {(p.metadata as any)?.category ?? 'Social platform'}
                        {connectedPlugins.has(p.name) && ' · already connected'}
                      </CardDescription>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-1">
                    {Object.entries(p.capabilities ?? {})
                      .filter(([k, v]) => typeof v === 'boolean' && v && k !== 'kind')
                      .slice(0, 5)
                      .map(([k]) => <Badge key={k}>{k}</Badge>)}
                  </div>
                  <div className="mt-4">
                    <Button size="sm" onClick={() => setAdding(p)}>
                      <Plus size={14} />
                      {connectedPlugins.has(p.name) ? 'Add another account' : 'Connect account'}
                    </Button>
                  </div>
                </Card>
              ))}
            </div>
          </section>
        </main>

        {/* Add-account dialog */}
        {adding && (
          <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50">
            <Card className="w-full max-w-md">
              <div className="flex items-start justify-between mb-3">
                <div>
                  <CardTitle>Add {adding.display_name} account</CardTitle>
                  <CardDescription>This creates a new connected account.</CardDescription>
                </div>
                <button onClick={() => setAdding(null)} className="text-ink-500 hover:text-ink-700">
                  <X size={18} />
                </button>
              </div>
              <div className="space-y-3">
                <div>
                  <label className="block text-xs text-ink-500 mb-1">Display label (in your workspace)</label>
                  <Input
                    value={label}
                    onChange={e => setLabel(e.target.value)}
                    placeholder={`e.g. "${adding.display_name} · Marketing"`}
                  />
                </div>
                <div>
                  <label className="block text-xs text-ink-500 mb-1">Account handle / page</label>
                  <Input
                    value={handle}
                    onChange={e => setHandle(e.target.value)}
                    placeholder="@acme-marketing"
                  />
                </div>
              </div>
              <div className="mt-5 flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setAdding(null)}>Cancel</Button>
                <Button onClick={add}>Create + start OAuth</Button>
              </div>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}

function AccountTile({ account }: { account: Platform }) {
  return (
    <div className="rounded-xl border border-ink-200 p-3 flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="text-sm font-medium truncate">{account.display_name}</span>
            {account.is_default && <Star size={12} className="text-amber-500" />}
          </div>
          {account.account_handle && (
            <div className="text-xs text-ink-500 truncate">{account.account_handle}</div>
          )}
        </div>
        <Badge tone={account.status === 'connected' ? 'success' : 'warning'}>
          {account.status}
        </Badge>
      </div>
      <div className="flex gap-1.5">
        <Button size="sm" variant="outline">Reconnect</Button>
        <Button size="sm" variant="ghost">Settings</Button>
      </div>
    </div>
  );
}
