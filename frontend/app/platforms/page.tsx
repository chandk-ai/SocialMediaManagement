'use client';
/**
 * Platforms page — production polish:
 *   • Smart empty state explaining "connected accounts" vs "available"
 *   • Per-account tile with status, last-used, "Used by N workflows", actions
 *   • Settings dialog: rename, set-as-default, edit tags, Page picker (Meta)
 *   • Test publish button (sends "Hello from SMMS — please ignore")
 *   • Inline OAuth hint per provider (Meta → FB consent, etc.)
 *   • "preview" badge on experimental connectors
 */
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { EmptyState } from '@/components/ui/EmptyState';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { useApi, api, ApiError } from '@/lib/api/client';
import { ApiErrorBanner } from '@/components/ui/ApiErrorBanner';
import type { PluginInfo, Platform, PlatformGroup } from '@/lib/api/types';
import {
  Plug, Plus, Star, X, AlertTriangle, Send, CheckCircle2, XCircle,
  Edit3, Trash2, Loader2, Info, Link as LinkIcon,
} from 'lucide-react';
import { useState } from 'react';
import Link from 'next/link';
import { mutate } from 'swr';
import { formatDateTime } from '@/lib/utils';

type Created = { id: string };

export default function PlatformsPage() {
  const { data: plugins, error: pluginsErr, mutate: retryPlugins } =
    useApi<PluginInfo[]>('/plugins?kind=platform');
  const { data: groups, error: groupsErr, mutate: retryGroups } =
    useApi<PlatformGroup[]>('/platforms/grouped');
  const [adding, setAdding] = useState<PluginInfo | null>(null);
  const [label, setLabel] = useState('');
  const [handle, setHandle] = useState('');
  const [busy, setBusy] = useState(false);
  const [warning, setWarning] = useState<string | null>(null);
  const [settings, setSettings] = useState<Platform | null>(null);

  async function add() {
    if (!adding) return;
    setBusy(true); setWarning(null);
    try {
      const created = await api.post<Created>('/platforms', {
        plugin_name: adding.name,
        display_name: label || `${adding.display_name} account`,
        account_handle: handle || null,
        config: {},
      });
      const redirectUri = `${window.location.origin}/oauth/callback`;
      try {
        const { authorize_url } = await api.post<{ authorize_url: string }>(
          `/platforms/${created.id}/oauth/start?redirect_uri=${encodeURIComponent(redirectUri)}`,
          {},
        );
        sessionStorage.setItem('oauth_platform_id', created.id);
        window.location.href = authorize_url;
        return;
      } catch (e: any) {
        const detail = e?.detail || e?.message || '';
        if (/no OAuth provider/i.test(detail)) {
          setWarning(
            `${adding.display_name} doesn't use OAuth. ` +
            `Account created — open Settings on the new tile to fill in credentials manually.`,
          );
        } else {
          setWarning(`OAuth couldn't start: ${detail || 'unknown error'}.`);
        }
      }
      await mutate('/platforms/grouped');
      setAdding(null); setLabel(''); setHandle('');
    } finally {
      setBusy(false);
    }
  }

  const connectedPlugins = new Set((groups ?? []).map(g => g.plugin_name));
  const totalAccounts = (groups ?? []).reduce((n, g) => n + g.accounts.length, 0);

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Platforms" />
        <main className="flex-1 overflow-y-auto p-6 space-y-8">
          <ApiErrorBanner error={pluginsErr || groupsErr}
                          retry={() => { retryPlugins(); retryGroups(); }} />

          <section>
            <div className="flex items-end justify-between mb-3">
              <div>
                <h2 className="text-base font-semibold text-ink-900">Connected accounts</h2>
                <p className="text-xs text-ink-500 mt-0.5">
                  Each tile is a single account. You can connect multiple accounts per provider
                  (e.g. two Instagram accounts → two tiles under Instagram).
                  {totalAccounts > 0 && <> {totalAccounts} total.</>}
                </p>
              </div>
            </div>
            {groups && groups.length === 0 ? (
              <EmptyState
                icon={<Plug size={32} />}
                title="No accounts connected"
                description="Pick a provider below and click Connect — you'll be redirected to the provider's consent screen."
              />
            ) : (
              <div className="space-y-4">
                {(groups ?? []).map(g => (
                  <Card key={g.plugin_name}>
                    <div className="flex items-center justify-between mb-3">
                      <div>
                        <CardTitle>
                          {g.display_name}
                          {(plugins ?? []).find(p => p.name === g.plugin_name)?.capabilities?.experimental && (
                            <Badge tone="warning" className="ml-2 text-[10px]">preview</Badge>
                          )}
                        </CardTitle>
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
                        <Plus size={14} /> Add another
                      </Button>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      {g.accounts.map(a => (
                        <AccountTile
                          key={a.id}
                          account={a}
                          pluginInfo={(plugins ?? []).find(p => p.name === a.plugin_name)}
                          onSettings={() => setSettings(a)}
                        />
                      ))}
                    </div>
                  </Card>
                ))}
              </div>
            )}
          </section>

          {/* Available providers */}
          <section>
            <h2 className="text-sm font-semibold text-ink-700 mb-3">Available platforms</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {(plugins ?? []).map(p => (
                <Card key={p.name}>
                  <div className="flex items-start justify-between">
                    <div>
                      <CardTitle>
                        {p.display_name}
                        {(p.capabilities as any)?.experimental && (
                          <Badge tone="warning" className="ml-2 text-[10px]">preview</Badge>
                        )}
                      </CardTitle>
                      <CardDescription>
                        {(p.metadata as any)?.category ?? 'Social platform'}
                        {connectedPlugins.has(p.name) && ' · already connected'}
                        {(p.capabilities as any)?.experimental && ' · publishing not yet wired'}
                      </CardDescription>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-1">
                    {Object.entries(p.capabilities ?? {})
                      .filter(([k, v]) => typeof v === 'boolean' && v && !['kind', 'experimental'].includes(k))
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
                  <CardDescription>Creates a new connected account and starts OAuth.</CardDescription>
                </div>
                <button onClick={() => setAdding(null)} className="text-ink-500 hover:text-ink-700">
                  <X size={18} />
                </button>
              </div>
              <div className="space-y-3">
                <div>
                  <label className="block text-xs text-ink-500 mb-1">Display label</label>
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
              {oauthHint(adding) && (
                <div className="mt-3 flex items-start gap-2 text-xs text-blue-800 bg-blue-50 border border-blue-200 rounded-lg p-2">
                  <Info size={14} className="mt-0.5 shrink-0" />
                  <span>{oauthHint(adding)}</span>
                </div>
              )}
              {warning && (
                <div className="mt-3 flex items-start gap-2 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg p-2">
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                  <span>{warning}</span>
                </div>
              )}
              <div className="mt-5 flex justify-end gap-2">
                <Button variant="ghost" onClick={() => { setAdding(null); setWarning(null); }} disabled={busy}>
                  Cancel
                </Button>
                <Button onClick={add} disabled={busy}>
                  {busy ? 'Starting…' : 'Create + start OAuth'}
                </Button>
              </div>
            </Card>
          </div>
        )}

        {settings && (
          <SettingsDialog
            account={settings}
            pluginInfo={(plugins ?? []).find(p => p.name === settings.plugin_name)}
            onClose={() => setSettings(null)}
          />
        )}
      </div>
    </div>
  );
}

function oauthHint(p: PluginInfo | null): string | null {
  if (!p) return null;
  if ((p.capabilities as any)?.experimental) {
    return `${p.display_name} is in preview — OAuth + account selection works, ` +
           `but publishing is not yet wired up. Posts targeting this platform will be held as "failed".`;
  }
  switch (p.name) {
    case 'instagram':
      return 'Instagram Business/Creator accounts authorize through Meta — the consent screen is served by facebook.com (by Meta\'s design).';
    case 'threads':
      return 'Threads has its own OAuth (independent of Facebook).';
    case 'facebook':
      return 'You\'ll choose which Page to grant access to. If you have multiple Pages, pick the one in Settings → Page after connecting.';
    case 'youtube':
      return 'YouTube authorizes through Google. Make sure your channel is associated with the signed-in Google account.';
    case 'tiktok':
      return 'TikTok requires a registered TikTok-for-Business app and an approved "content publish" scope.';
    case 'telegram':
      return 'Telegram bots use a long-lived bot token — get one from @BotFather, no OAuth.';
    case 'slack':
    case 'discord':
      return 'Easiest: use a webhook URL from the channel\'s integration settings.';
    default:
      return null;
  }
}

/* ───────── Account tile ─────────────────────────────────────────────── */

function AccountTile({ account, pluginInfo, onSettings }: {
  account: Platform;
  pluginInfo: PluginInfo | undefined;
  onSettings: () => void;
}) {
  const { data: usage } = useApi<{ count: number; workflows: Array<{ id: string; name: string }> }>(`/platforms/${account.id}/usage`);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [test, setTest] = useState<{ ok: boolean; error?: string; url?: string } | null>(null);

  async function reconnect() {
    setBusy('reconnect'); setErr(null);
    try {
      const redirectUri = `${window.location.origin}/oauth/callback`;
      sessionStorage.setItem('oauth_platform_id', account.id);
      const { authorize_url } = await api.post<{ authorize_url: string }>(
        `/platforms/${account.id}/oauth/start?redirect_uri=${encodeURIComponent(redirectUri)}`,
        {},
      );
      window.location.href = authorize_url;
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Reconnect failed.');
      setBusy(null);
    }
  }

  async function testPublish() {
    setBusy('test'); setErr(null); setTest(null);
    try {
      const res = await api.post<{ ok: boolean; error?: string; url?: string }>(
        `/platforms/${account.id}/test_publish`, {},
      );
      setTest(res);
    } catch (e) {
      const ae = e as ApiError;
      setTest({ ok: false, error: ae?.detail || (e as Error)?.message || 'Test failed.' });
    } finally { setBusy(null); }
  }

  async function remove() {
    const usedBy = usage?.count ?? 0;
    const msg = usedBy > 0
      ? `"${account.display_name}" is used by ${usedBy} workflow${usedBy !== 1 ? 's' : ''}. Remove anyway?`
      : `Remove ${account.display_name}? This disconnects the account but keeps any posts already published.`;
    if (!confirm(msg)) return;
    try {
      await api.del(`/platforms/${account.id}`);
      await mutate('/platforms/grouped');
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Remove failed.');
    }
  }

  const isExperimental = (pluginInfo?.capabilities as any)?.experimental;
  const cfg = account.config as any;
  const needsPagePicker =
    account.plugin_name === 'instagram' && Array.isArray(cfg?.__pages_available__) && !cfg?.ig_user_id;
  const fbNeedsPicker =
    account.plugin_name === 'facebook' && Array.isArray(cfg?.__pages_available__) && !cfg?.page_id;

  return (
    <div className="rounded-xl border border-ink-200 p-3 flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-sm font-medium truncate">{account.display_name}</span>
            {account.is_default && <Star size={12} className="text-amber-500" aria-label="default" />}
            {(account.tags ?? []).map(t => (
              <span key={t} className="text-[10px] px-1.5 py-0.5 rounded bg-ink-100 text-ink-700">{t}</span>
            ))}
          </div>
          {account.account_handle && (
            <div className="text-xs text-ink-500 truncate">{account.account_handle}</div>
          )}
          {(usage?.count ?? 0) > 0 && (
            <div className="text-[11px] text-ink-500 mt-1">
              Used by {usage!.count} workflow{usage!.count !== 1 && 's'}
            </div>
          )}
        </div>
        <Badge tone={
          isExperimental ? 'warning' :
          account.status === 'connected' ? 'success' :
          account.status === 'expired' || account.status === 'error' ? 'danger' : 'warning'
        }>
          {isExperimental ? 'preview' : account.status}
        </Badge>
      </div>

      {(needsPagePicker || fbNeedsPicker) && (
        <div className="flex items-start gap-2 text-[11px] text-amber-800 bg-amber-50 border border-amber-200 rounded p-2">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          <span>
            Multiple Pages available — open Settings to pick which Page this account publishes to.
          </span>
        </div>
      )}

      <div className="flex flex-wrap gap-1.5">
        <Button size="sm" variant="outline" onClick={reconnect} disabled={busy !== null}>
          <LinkIcon size={12} /> {account.status === 'connected' ? 'Reconnect' : 'Connect'}
        </Button>
        <Button size="sm" variant="outline" onClick={testPublish} disabled={busy !== null || isExperimental}>
          {busy === 'test' ? <Loader2 size={12} className="animate-spin" /> :
           test?.ok === true ? <CheckCircle2 size={12} className="text-emerald-600" /> :
           test?.ok === false ? <XCircle size={12} className="text-red-600" /> :
           <Send size={12} />}
          Test publish
        </Button>
        <Button size="sm" variant="ghost" onClick={onSettings}>
          <Edit3 size={12} /> Settings
        </Button>
        <Button size="sm" variant="ghost" onClick={remove}>
          <Trash2 size={12} /> Remove
        </Button>
      </div>

      {test?.ok === true && (
        <div className="flex items-start gap-2 text-[11px] text-emerald-800 bg-emerald-50 border border-emerald-200 rounded p-2">
          <CheckCircle2 size={12} className="mt-0.5 shrink-0" />
          <span>
            Test post published.{' '}
            {test.url && <a href={test.url} target="_blank" rel="noreferrer" className="underline">View →</a>}
          </span>
        </div>
      )}
      {test?.ok === false && (
        <div className="flex items-start gap-2 text-[11px] text-red-800 bg-red-50 border border-red-200 rounded p-2">
          <XCircle size={12} className="mt-0.5 shrink-0" />
          <span className="break-words">{test.error}</span>
        </div>
      )}
      {err && (
        <div className="flex items-start gap-2 text-[11px] text-red-800 bg-red-50 border border-red-200 rounded p-2">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          <span>{err}</span>
        </div>
      )}
    </div>
  );
}

/* ───────── Settings dialog (rename / default / tags / Page picker) ─── */

function SettingsDialog({ account, pluginInfo, onClose }: {
  account: Platform;
  pluginInfo: PluginInfo | undefined;
  onClose: () => void;
}) {
  const [displayName, setDisplayName] = useState(account.display_name);
  const [tags, setTags] = useState((account.tags ?? []).join(', '));
  const [isDefault, setIsDefault] = useState(account.is_default ?? false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Page picker (Meta family)
  const cfg = account.config as any;
  const availablePages: Array<{ id: string; name: string; instagram_business_account?: { id?: string; username?: string } }> =
    Array.isArray(cfg?.__pages_available__) ? cfg.__pages_available__ : [];
  const currentPageId = cfg?.page_id || '';
  const [pagePick, setPagePick] = useState<string>(currentPageId);

  async function save() {
    setBusy(true); setErr(null);
    try {
      const tagList = tags.split(',').map(s => s.trim()).filter(Boolean);
      await api.patch(`/platforms/${account.id}`, {
        display_name: displayName,
        tags: tagList,
        is_default: isDefault,
      });
      // Switch Page if user picked a different one
      if (pagePick && pagePick !== currentPageId) {
        await api.post(`/platforms/${account.id}/page/select`, { page_id: pagePick });
      }
      await mutate('/platforms/grouped');
      onClose();
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Save failed.');
    } finally { setBusy(false); }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50" onClick={onClose}>
      <Card className="w-full max-w-md" onClick={(e: any) => e.stopPropagation()}>
        <div className="flex items-start justify-between mb-3">
          <div>
            <CardTitle>{account.display_name}</CardTitle>
            <CardDescription>
              {pluginInfo?.display_name ?? account.plugin_name} · {account.status}
            </CardDescription>
          </div>
          <button onClick={onClose} className="text-ink-500 hover:text-ink-700">
            <X size={18} />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-xs text-ink-500 mb-1">Display label</label>
            <Input value={displayName} onChange={(e: any) => setDisplayName(e.target.value)} />
          </div>

          <div>
            <label className="block text-xs text-ink-500 mb-1">
              Tags <span className="text-ink-400">comma-separated, e.g. "marketing, primary"</span>
            </label>
            <Input value={tags} onChange={(e: any) => setTags(e.target.value)} placeholder="marketing, primary" />
            <p className="text-[11px] text-ink-500 mt-1">
              Use these in workflow target rules — e.g. "post to all accounts tagged 'marketing'".
            </p>
          </div>

          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={isDefault}
              onChange={(e) => setIsDefault(e.target.checked)}
            />
            <span>
              Default account for {pluginInfo?.display_name ?? account.plugin_name}
              <span className="block text-[11px] text-ink-500">
                Workflows that don't pick a specific account use the default.
              </span>
            </span>
          </label>

          {availablePages.length > 0 && (
            <div className="border-t border-ink-100 pt-3">
              <label className="block text-xs font-semibold text-ink-700 mb-1">
                {account.plugin_name === 'instagram' ? 'Linked Page (and Instagram account)' : 'Page'}
              </label>
              <p className="text-[11px] text-ink-500 mb-2">
                {availablePages.length === 1
                  ? "There's one Page connected — already selected."
                  : "Pick which Page this connection should publish to."}
              </p>
              <select
                className="input"
                value={pagePick}
                onChange={(e) => setPagePick(e.target.value)}
              >
                <option value="">— pick a Page —</option>
                {availablePages.map(p => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                    {p.instagram_business_account?.username && ` · @${p.instagram_business_account.username}`}
                  </option>
                ))}
              </select>
            </div>
          )}

          {err && (
            <div className="flex items-start gap-2 text-xs text-red-800 bg-red-50 border border-red-200 rounded p-2">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span>{err}</span>
            </div>
          )}
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={save} disabled={busy}>
            {busy ? 'Saving…' : 'Save changes'}
          </Button>
        </div>
      </Card>
    </div>
  );
}
