'use client';
/**
 * Settings page — org-level configuration with LLM API key management.
 *
 * The LLM Keys section is the most important addition: it lets users plug in
 * their Anthropic / OpenAI / Google / etc. keys, which the agents pull at
 * runtime to do real content generation. Keys are encrypted at rest via
 * the backend's TokenVault.
 */
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { useApi, api, ApiError } from '@/lib/api/client';
import {
  Key, Sparkles, CheckCircle2, AlertTriangle, Loader2, Trash2, Eye, EyeOff,
} from 'lucide-react';
import { useState } from 'react';
import { mutate } from 'swr';

type LlmKeyInfo = {
  provider: string;
  is_set: boolean;
  last_4: string | null;
};

type LlmKeysResponse = {
  providers: LlmKeyInfo[];
  known_providers: string[];
  preferred_provider: string | null;
  preferred_model: string | null;
};

const PROVIDER_LABELS: Record<string, { label: string; help: string; defaultModel: string }> = {
  anthropic: {
    label: 'Anthropic Claude',
    help: 'Get a key at console.anthropic.com → API Keys.',
    defaultModel: 'claude-sonnet-4-6',
  },
  openai: {
    label: 'OpenAI',
    help: 'Get a key at platform.openai.com → API keys.',
    defaultModel: 'gpt-4o',
  },
  google: {
    label: 'Google AI Studio',
    help: 'Get a key at aistudio.google.com → API keys.',
    defaultModel: 'gemini-1.5-pro',
  },
  gemini: {
    label: 'Gemini (alias)',
    help: 'Same key as Google AI Studio.',
    defaultModel: 'gemini-1.5-pro',
  },
  groq: {
    label: 'Groq (fast inference)',
    help: 'Get a key at console.groq.com → API keys.',
    defaultModel: 'llama-3.1-70b-versatile',
  },
  azure_openai: {
    label: 'Azure OpenAI',
    help: 'Set the resource API key. Endpoint configured separately.',
    defaultModel: 'gpt-4o',
  },
  huggingface: {
    label: 'Hugging Face',
    help: 'Get a token at huggingface.co/settings/tokens.',
    defaultModel: 'meta-llama/Llama-3.1-70B-Instruct',
  },
  ollama: {
    label: 'Ollama (local)',
    help: 'No API key needed for local models. Just set this provider as preferred.',
    defaultModel: 'llama3.1',
  },
  bedrock: {
    label: 'AWS Bedrock',
    help: 'Use AWS_ACCESS_KEY/SECRET on the backend; this slot is informational.',
    defaultModel: 'anthropic.claude-3-5-sonnet-20240620-v1:0',
  },
};

export default function SettingsPage() {
  const { data: llmKeys } = useApi<LlmKeysResponse>('/llm-keys');

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Settings" />
        <main className="flex-1 overflow-y-auto p-6 space-y-6 max-w-3xl">
          {/* LLM Keys — the important new section */}
          <Card>
            <div className="flex items-start gap-3">
              <div className="size-9 rounded-lg bg-accent-muted flex items-center justify-center text-accent shrink-0">
                <Sparkles size={18} />
              </div>
              <div className="flex-1">
                <CardTitle>AI / LLM providers</CardTitle>
                <CardDescription>
                  Add your own API keys. The agents use these to draft posts, evaluate quality,
                  and revise based on feedback. Keys are encrypted at rest and never returned in plaintext.
                </CardDescription>
              </div>
            </div>

            {/* Preferred provider/model */}
            <PreferredPicker
              providers={(llmKeys?.providers ?? []).filter(p => p.is_set)}
              currentProvider={llmKeys?.preferred_provider ?? null}
              currentModel={llmKeys?.preferred_model ?? null}
            />

            {/* Per-provider key rows */}
            <div className="mt-5 space-y-3">
              {(llmKeys?.known_providers ?? []).map(name => {
                const info = (llmKeys?.providers ?? []).find(p => p.provider === name);
                return (
                  <ProviderKeyRow
                    key={name}
                    name={name}
                    info={info}
                  />
                );
              })}
            </div>
          </Card>

          {/* Org info */}
          <Card>
            <CardTitle>Organization</CardTitle>
            <CardDescription>Basic information about your workspace</CardDescription>
            <div className="mt-4 space-y-3">
              <Input placeholder="Organization name" defaultValue="Acme Corp" />
              <Input placeholder="Slug" defaultValue="acme" />
              <Button>Save</Button>
            </div>
          </Card>

          <LlmBudgetCard />


          <Card>
            <CardTitle>Identity provider</CardTitle>
            <CardDescription>Single sign-on via Okta / OIDC</CardDescription>
            <p className="mt-2 text-sm text-ink-500">
              Configured via environment variables on the backend. See <code>docs/SECURITY.md</code>.
            </p>
          </Card>
        </main>
      </div>
    </div>
  );
}

function PreferredPicker({ providers, currentProvider, currentModel }: {
  providers: LlmKeyInfo[];
  currentProvider: string | null;
  currentModel: string | null;
}) {
  const [provider, setProvider] = useState(currentProvider ?? '');
  const [model, setModel] = useState(currentModel ?? '');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);

  async function save() {
    setBusy(true); setErr(null); setOk(false);
    try {
      await api.put('/llm-keys/preferred', {
        provider: provider || null,
        model: model || null,
      });
      setOk(true);
      await mutate('/llm-keys');
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Save failed.');
    } finally { setBusy(false); }
  }

  return (
    <div className="mt-4 rounded-xl border border-ink-100 p-3 bg-ink-50">
      <div className="text-xs font-medium text-ink-700 mb-2">Default LLM for new workflows</div>
      <div className="flex flex-wrap items-end gap-2">
        <div>
          <label className="block text-[11px] text-ink-500 mb-1">Provider</label>
          <select
            className="input min-w-[180px]"
            value={provider}
            onChange={(e) => {
              setProvider(e.target.value);
              if (e.target.value) {
                setModel(PROVIDER_LABELS[e.target.value]?.defaultModel ?? '');
              }
            }}
          >
            <option value="">— pick a provider —</option>
            {providers.map(p => (
              <option key={p.provider} value={p.provider}>
                {PROVIDER_LABELS[p.provider]?.label ?? p.provider}
              </option>
            ))}
          </select>
        </div>
        <div className="flex-1 min-w-[200px]">
          <label className="block text-[11px] text-ink-500 mb-1">Model</label>
          <Input value={model} onChange={(e: any) => setModel(e.target.value)} placeholder="e.g. claude-sonnet-4-6" />
        </div>
        <Button size="sm" onClick={save} disabled={busy}>
          {busy ? <Loader2 size={12} className="animate-spin" /> : null} Save
        </Button>
      </div>
      {err && (
        <div className="mt-2 flex items-start gap-2 text-[11px] text-red-800 bg-red-50 border border-red-200 rounded p-2">
          <AlertTriangle size={12} className="mt-0.5" /> <span>{err}</span>
        </div>
      )}
      {ok && (
        <div className="mt-2 flex items-start gap-2 text-[11px] text-emerald-800 bg-emerald-50 border border-emerald-200 rounded p-2">
          <CheckCircle2 size={12} className="mt-0.5" /> Saved.
        </div>
      )}
      {providers.length === 0 && (
        <p className="text-[11px] text-ink-500 mt-2">
          Add an API key below first. Until you set a real provider, workflows fall back to the mock LLM (placeholder text).
        </p>
      )}
    </div>
  );
}

type LlmUsage = {
  billing_month: string;
  mtd_spend_usd: number;
  budget_usd: number;
  remaining_usd: number;
  over_budget: boolean;
};

function LlmBudgetCard() {
  const { data: usage } = useApi<LlmUsage>('/llm-usage');
  const [draft, setDraft] = useState<string>('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);

  // Hydrate the input from server state once it loads.
  const cap = usage?.budget_usd ?? 0;
  const spent = usage?.mtd_spend_usd ?? 0;
  // Clamp to 0..100 so the bar never overflows visually even on overruns.
  const pct = cap > 0 ? Math.min(100, Math.round((spent / cap) * 100)) : 0;

  async function save() {
    setBusy(true); setErr(null); setOk(false);
    const v = Number(draft || cap);
    if (!Number.isFinite(v) || v < 0) {
      setErr('Enter a non-negative dollar amount.');
      setBusy(false);
      return;
    }
    try {
      await api.put('/llm-usage/budget', { monthly_llm_budget_usd: v });
      setOk(true);
      setDraft('');
      await mutate('/llm-usage');
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Save failed.');
    } finally { setBusy(false); }
  }

  return (
    <Card>
      <CardTitle>LLM budget</CardTitle>
      <CardDescription>
        Hard cap on AI spend across every workflow this month. Hitting the cap
        pauses all agent runs until you raise it. Set to <code>0</code> to
        disable the limit.
      </CardDescription>

      {/* Spend meter */}
      <div className="mt-4 rounded-xl border border-ink-100 p-3 bg-ink-50">
        <div className="flex items-end justify-between mb-2">
          <div>
            <div className="text-[11px] uppercase tracking-wider text-ink-500">
              Month-to-date spend
            </div>
            <div className="text-xl font-semibold tabular-nums">
              ${spent.toFixed(2)}
              {cap > 0 && (
                <span className="text-sm text-ink-500 font-normal"> / ${cap.toFixed(2)}</span>
              )}
            </div>
          </div>
          {usage?.over_budget && (
            <Badge tone="danger">Cap reached — runs paused</Badge>
          )}
        </div>
        {cap > 0 ? (
          <div className="h-1.5 rounded-full bg-ink-100 overflow-hidden">
            <div
              className={`h-full transition-all ${
                usage?.over_budget ? 'bg-red-500' : pct >= 80 ? 'bg-amber-500' : 'bg-emerald-500'
              }`}
              style={{ width: `${pct}%` }}
            />
          </div>
        ) : (
          <p className="text-[11px] text-ink-500">No cap set — usage is unlimited.</p>
        )}
      </div>

      {/* Edit cap */}
      <div className="mt-4 flex items-end gap-3">
        <div>
          <label className="block text-[11px] text-ink-500 mb-1">Monthly cap</label>
          <Input
            type="number"
            min={0}
            step="1"
            value={draft || (cap || '').toString()}
            onChange={(e: any) => setDraft(e.target.value)}
            className="w-40"
          />
        </div>
        <span className="text-sm text-ink-500 mb-2">USD / month</span>
        <Button size="sm" onClick={save} disabled={busy}>
          {busy ? <Loader2 size={12} className="animate-spin" /> : null} Save
        </Button>
      </div>
      {err && (
        <div className="mt-2 flex items-start gap-2 text-[11px] text-red-800 bg-red-50 border border-red-200 rounded p-2">
          <AlertTriangle size={12} className="mt-0.5" /> <span>{err}</span>
        </div>
      )}
      {ok && (
        <div className="mt-2 flex items-start gap-2 text-[11px] text-emerald-800 bg-emerald-50 border border-emerald-200 rounded p-2">
          <CheckCircle2 size={12} className="mt-0.5" /> Saved.
        </div>
      )}
    </Card>
  );
}

function ProviderKeyRow({ name, info }: { name: string; info: LlmKeyInfo | undefined }) {
  const meta = PROVIDER_LABELS[name] ?? { label: name, help: '', defaultModel: '' };
  const [value, setValue] = useState('');
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [savedJustNow, setSavedJustNow] = useState(false);

  async function save() {
    if (!value.trim()) {
      setErr('API key cannot be empty.');
      return;
    }
    setBusy('save'); setErr(null);
    try {
      await api.put(`/llm-keys/${name}`, { api_key: value });
      setValue('');
      setSavedJustNow(true);
      setTimeout(() => setSavedJustNow(false), 3000);
      await mutate('/llm-keys');
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Save failed.');
    } finally { setBusy(null); }
  }

  async function remove() {
    if (!confirm(`Remove ${meta.label} API key?`)) return;
    setBusy('delete'); setErr(null);
    try {
      await api.del(`/llm-keys/${name}`);
      await mutate('/llm-keys');
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Delete failed.');
    } finally { setBusy(null); }
  }

  return (
    <div className="rounded-xl border border-ink-100 p-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Key size={14} className="text-ink-500" />
            <span className="font-medium text-sm">{meta.label}</span>
            {info?.is_set && (
              <Badge tone="success">configured · ····{info.last_4}</Badge>
            )}
          </div>
          {meta.help && <p className="text-[11px] text-ink-500 mt-0.5">{meta.help}</p>}
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Input
              type={show ? 'text' : 'password'}
              value={value}
              onChange={(e: any) => setValue(e.target.value)}
              placeholder={info?.is_set ? 'Replace existing…' : 'sk-... or similar'}
              className="pr-8 min-w-[260px]"
            />
            <button
              type="button"
              onClick={() => setShow(s => !s)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-500 hover:text-ink-700"
              aria-label={show ? 'Hide' : 'Show'}
            >
              {show ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
          </div>
          <Button size="sm" onClick={save} disabled={busy !== null}>
            {busy === 'save' ? <Loader2 size={12} className="animate-spin" /> : null}
            {info?.is_set ? 'Update' : 'Save'}
          </Button>
          {info?.is_set && (
            <Button size="sm" variant="ghost" onClick={remove} disabled={busy !== null}>
              <Trash2 size={12} /> Remove
            </Button>
          )}
        </div>
      </div>
      {err && (
        <div className="mt-2 flex items-start gap-2 text-[11px] text-red-800 bg-red-50 border border-red-200 rounded p-2">
          <AlertTriangle size={12} className="mt-0.5" /> <span>{err}</span>
        </div>
      )}
      {savedJustNow && (
        <div className="mt-2 flex items-start gap-2 text-[11px] text-emerald-800 bg-emerald-50 border border-emerald-200 rounded p-2">
          <CheckCircle2 size={12} className="mt-0.5" /> Saved.
        </div>
      )}
    </div>
  );
}
