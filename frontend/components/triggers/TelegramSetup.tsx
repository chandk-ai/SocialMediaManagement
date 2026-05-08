'use client';
/**
 * Two-step Telegram setup wizard. The whole flow is designed to take ~3 minutes:
 *
 *   Step 1 — Paste token + pick workflow + click Create.
 *            We mint a random 32-char secret_token in the browser; both the
 *            token and the secret are saved into the trigger's `config`.
 *
 *   Step 2 — We compute the webhook URL the trigger now lives at and render
 *            a single curl command (with token + secret + URL pre-filled).
 *            User clicks Copy, runs the command in their terminal, and the
 *            Telegram bot is wired up. Their next message to the bot fires
 *            the workflow.
 *
 * The webhook URL goes through the Next.js proxy at /api/proxy/webhooks/...,
 * which rewrites to the backend. That keeps everything on a single origin —
 * no CORS, no separate backend hostname for the user to figure out.
 */
import { useMemo, useState } from 'react';
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { ApiError, api } from '@/lib/api/client';
import {
  AlertTriangle, ArrowRight, CheckCircle2, Copy, Loader2, Send,
} from 'lucide-react';

type Workflow = { id: string; name: string };

type Props = {
  workflows: Workflow[];
  /** Same review-channel plugin list the generic dialog uses. */
  reviewChannelOptions: { name: string; display_name: string }[];
  onClose: () => void;
  onCreated: () => Promise<void>;
};

type CreatedTrigger = {
  id: string;
  display_name: string;
  config: { bot_token?: string; secret_token?: string };
};

export function TelegramSetup({ workflows, reviewChannelOptions, onClose, onCreated }: Props) {
  const [step, setStep] = useState<'form' | 'webhook'>('form');
  const [created, setCreated] = useState<CreatedTrigger | null>(null);

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50">
      <Card className="w-full max-w-2xl">
        <CardTitle className="flex items-center gap-2">
          <Send size={16} /> Set up Telegram trigger
        </CardTitle>
        <CardDescription>
          {step === 'form'
            ? 'Paste your bot token and pick the workflow it should fire. Three minutes from BotFather to a working approval flow.'
            : 'One last step — register the webhook with Telegram. Copy the command below and run it in your terminal.'}
        </CardDescription>

        {step === 'form' && (
          <Step1Form
            workflows={workflows}
            reviewChannelOptions={reviewChannelOptions}
            onCancel={onClose}
            onCreated={(t) => { setCreated(t); setStep('webhook'); }}
          />
        )}
        {step === 'webhook' && created && (
          <Step2Webhook
            trigger={created}
            onDone={async () => {
              await onCreated();
              onClose();
            }}
          />
        )}
      </Card>
    </div>
  );
}

// ── Step 1 — capture bot token, mint secret, create trigger ──────────────
function Step1Form({
  workflows, reviewChannelOptions, onCancel, onCreated,
}: {
  workflows: Workflow[];
  reviewChannelOptions: { name: string; display_name: string }[];
  onCancel: () => void;
  onCreated: (t: CreatedTrigger) => void;
}) {
  const [botToken, setBotToken] = useState('');
  const [workflowId, setWorkflowId] = useState('');
  const [displayName, setDisplayName] = useState('Telegram trigger');
  const [reviewChannel, setReviewChannel] = useState('telegram');
  const [allowedChats, setAllowedChats] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Mint a stable secret_token for this dialog session — regenerating on
  // each render would mean the curl in step 2 doesn't match what we saved.
  const secretToken = useMemo(() => randomToken(32), []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    if (!botToken.trim() || !botToken.includes(':')) {
      setErr('Bot token looks wrong — should be like 123456:ABC-...');
      return;
    }
    if (!workflowId) {
      setErr('Pick a workflow.');
      return;
    }
    setBusy(true);
    try {
      const allowed = allowedChats.split(',').map(s => s.trim()).filter(Boolean);
      const resp = await api.post<CreatedTrigger>('/triggers', {
        workflow_id: workflowId,
        plugin_name: 'telegram',
        display_name: displayName,
        config: {
          bot_token: botToken.trim(),
          secret_token: secretToken,
          allowed_chat_ids: allowed,
        },
        allowed_senders: allowed,
        review_channel: reviewChannel || null,
        // For Telegram review, the recipient is the chat_id the bot DMs back.
        // If the user gave one allowed chat, default to it; otherwise blank
        // (the trigger will record the first sender as the review recipient
        // when a directive comes in — handled by ReviewService).
        review_recipient: allowed[0] || null,
      });
      onCreated(resp);
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Create failed.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3">
      <div className="md:col-span-2">
        <label className="block text-xs text-ink-500 mb-1">Bot token</label>
        <Input
          type="password"
          value={botToken}
          onChange={e => setBotToken(e.target.value)}
          placeholder="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
          autoComplete="off"
          required
        />
        <p className="mt-1 text-[11px] text-ink-500">
          Open Telegram, message <code>@BotFather</code>, run <code>/newbot</code>, copy the token.
          Stored encrypted; never visible to other org members.
        </p>
      </div>

      <div>
        <label className="block text-xs text-ink-500 mb-1">Workflow</label>
        <select
          className="input"
          value={workflowId}
          onChange={e => setWorkflowId(e.target.value)}
          required
        >
          <option value="">Select…</option>
          {workflows.map(w => (
            <option key={w.id} value={w.id}>{w.name}</option>
          ))}
        </select>
      </div>

      <div>
        <label className="block text-xs text-ink-500 mb-1">Display name</label>
        <Input value={displayName} onChange={e => setDisplayName(e.target.value)} />
      </div>

      <div className="md:col-span-2">
        <label className="block text-xs text-ink-500 mb-1">
          Allowed chat ids <span className="text-ink-400">(comma-separated, optional)</span>
        </label>
        <Input
          value={allowedChats}
          onChange={e => setAllowedChats(e.target.value)}
          placeholder="123456789, 987654321"
        />
        <p className="mt-1 text-[11px] text-ink-500">
          Empty = anyone who knows the bot can fire the workflow. To find your chat id,
          message <code>@userinfobot</code>.
        </p>
      </div>

      <div className="md:col-span-2">
        <label className="block text-xs text-ink-500 mb-1">Review channel</label>
        <select
          className="input"
          value={reviewChannel}
          onChange={e => setReviewChannel(e.target.value)}
        >
          {reviewChannelOptions.map(r => (
            <option key={r.name} value={r.name}>{r.display_name}</option>
          ))}
        </select>
        <p className="mt-1 text-[11px] text-ink-500">
          Where drafts come back for approval. Default <strong>Telegram</strong> means the same bot
          will message you back with ✅ Approve / ✏️ Revise / ❌ Reject buttons.
        </p>
      </div>

      {err && (
        <div className="md:col-span-2 flex items-start gap-2 text-[12px] text-red-800 bg-red-50 border border-red-200 rounded p-2">
          <AlertTriangle size={12} className="mt-0.5" /> <span>{err}</span>
        </div>
      )}

      <div className="md:col-span-2 mt-2 flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
        <Button type="submit" disabled={busy}>
          {busy ? <Loader2 size={14} className="animate-spin" /> : null}
          Create trigger <ArrowRight size={14} />
        </Button>
      </div>
    </form>
  );
}

// ── Step 2 — show the curl command to register the webhook ───────────────
function Step2Webhook({
  trigger, onDone,
}: {
  trigger: CreatedTrigger;
  onDone: () => Promise<void>;
}) {
  const webhookUrl = useMemo(() => {
    if (typeof window === 'undefined') return '';
    return `${window.location.origin}/api/proxy/webhooks/telegram/${trigger.id}`;
  }, [trigger.id]);

  const curl = useMemo(() => {
    const token = trigger.config.bot_token || '<bot_token>';
    const secret = trigger.config.secret_token || '<secret>';
    return [
      `curl 'https://api.telegram.org/bot${token}/setWebhook' \\`,
      `  -d url='${webhookUrl}' \\`,
      `  -d secret_token='${secret}'`,
    ].join('\n');
  }, [trigger, webhookUrl]);

  const [copied, setCopied] = useState(false);
  async function copyCurl() {
    try {
      await navigator.clipboard.writeText(curl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* ignore */ }
  }

  return (
    <div className="mt-4 space-y-4">
      <div className="rounded-lg bg-ink-50 border border-ink-100 p-4">
        <div className="flex items-center justify-between mb-2">
          <div className="text-xs uppercase tracking-wider text-ink-500">
            Run this in your terminal
          </div>
          <Button size="sm" variant="outline" onClick={copyCurl}>
            {copied ? <CheckCircle2 size={12} /> : <Copy size={12} />}
            {copied ? 'Copied' : 'Copy'}
          </Button>
        </div>
        <pre className="font-mono text-[11px] leading-5 text-ink-800 whitespace-pre-wrap break-all">
          {curl}
        </pre>
      </div>

      <ol className="text-sm space-y-1 list-decimal pl-4 text-ink-700">
        <li>Run the command above. You should see <code>{`{"ok":true,"result":true}`}</code>.</li>
        <li>Open your bot in Telegram and send any message — that fires the workflow.</li>
        <li>Drafts come back to the same bot with one-tap Approve / Revise / Reject buttons.</li>
      </ol>

      <div className="rounded border border-amber-200 bg-amber-50 p-3 text-[12px] text-amber-900">
        <strong>Heads up:</strong> the secret token above is what verifies inbound webhooks
        actually came from Telegram. We've already saved it to the trigger config; if you
        regenerate the webhook later, update the trigger config to match.
      </div>

      <div className="flex justify-end">
        <Button onClick={onDone}>I've registered it — done</Button>
      </div>
    </div>
  );
}

// ── helpers ──────────────────────────────────────────────────────────────
function randomToken(len: number): string {
  // crypto.getRandomValues is available in every browser Next 14 supports.
  const bytes = new Uint8Array(len);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const c: any = (globalThis as any).crypto;
  if (c?.getRandomValues) {
    c.getRandomValues(bytes);
  } else {
    for (let i = 0; i < len; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  // URL-safe base32-ish (avoid + / = which break shell quoting).
  const alpha = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  let out = '';
  for (let i = 0; i < len; i++) out += alpha[bytes[i] % alpha.length];
  return out;
}
