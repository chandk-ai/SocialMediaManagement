'use client';
/**
 * In-app trainer — 7-step interactive walkthrough.
 *
 * Each step has a goal, a couple of action buttons that deep-link into the
 * matching dashboard page, and a "Mark as done" toggle. Progress is stored
 * in localStorage so the user can pause and resume.
 */
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import {
  CheckCircle2, Circle, Sparkles, Plug, Database, Workflow,
  Zap, MessageSquareCheck, BarChart3, ArrowRight,
} from 'lucide-react';

const STORAGE_KEY = 'smms.onboarding.v1';

type Step = {
  id: string;
  icon: any;
  title: string;
  body: string;
  actions: { label: string; href: string; primary?: boolean }[];
};

const STEPS: Step[] = [
  {
    id: 'welcome',
    icon: Sparkles,
    title: 'Welcome to SMMS',
    body: 'You will get a working publish loop in about 10 minutes. Each step takes 1–3 minutes. You can leave this page and come back any time — your progress is saved locally.',
    actions: [],
  },
  {
    id: 'platform',
    icon: Plug,
    title: 'Connect a social account',
    body: 'Add LinkedIn, X, Facebook, Instagram, YouTube, TikTok or any of the 16 supported networks. You can repeat this step to connect multiple accounts on the same platform — all of them get their own row and can be tagged for later targeting.',
    actions: [
      { label: 'Add a platform', href: '/platforms', primary: true },
    ],
  },
  {
    id: 'source',
    icon: Database,
    title: 'Connect a content source',
    body: 'Sources are where the agents pull reference material from. RSS feeds and Notion databases are the easiest places to start. You can also skip this step and drive runs purely from chat directives.',
    actions: [
      { label: 'Add a source', href: '/sources', primary: true },
    ],
  },
  {
    id: 'workflow',
    icon: Workflow,
    title: 'Build your first workflow',
    body: 'A workflow ties sources → agents → target accounts → schedule. Start manual — once you trust the output, switch to Optimal scheduling so the system picks the best time per platform.',
    actions: [
      { label: 'Create workflow', href: '/workflows/new', primary: true },
      { label: 'View existing', href: '/workflows' },
    ],
  },
  {
    id: 'trigger',
    icon: Zap,
    title: 'Add a chat trigger (optional but powerful)',
    body: 'Wire up WhatsApp / Telegram / Instagram so you can text the bot a directive ("post about our Q4 launch on every IG and FB account"). The same channel is used to send the draft back for approval.',
    actions: [
      { label: 'Add a trigger', href: '/triggers', primary: true },
    ],
  },
  {
    id: 'review',
    icon: MessageSquareCheck,
    title: 'Review and approve',
    body: 'Workflows that need approval pause and ping you. Approve / Revise (with feedback) / Reject — the agents either publish, re-run with your notes, or cancel. Reviews can be resolved here or right inside WhatsApp / Telegram.',
    actions: [
      { label: 'Open reviews', href: '/reviews', primary: true },
    ],
  },
  {
    id: 'analytics',
    icon: BarChart3,
    title: 'See what is working',
    body: 'Analytics shows publish volume, success vs. failed per platform, and your 14-day throughput. Calendar is the month-grid of scheduled and published posts. Audit log is the timeline of every state change.',
    actions: [
      { label: 'Open analytics', href: '/analytics', primary: true },
      { label: 'Open calendar', href: '/calendar' },
    ],
  },
];

export default function OnboardingPage() {
  const [done, setDone] = useState<Set<string>>(new Set());
  const [current, setCurrent] = useState(0);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        setDone(new Set(parsed.done ?? []));
        setCurrent(parsed.current ?? 0);
      }
    } catch {/* ignore */}
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        done: Array.from(done), current,
      }));
    } catch {/* ignore */}
  }, [done, current]);

  const total = STEPS.length;
  const completed = done.size;
  const pct = Math.round((completed / total) * 100);

  function toggle(stepId: string) {
    setDone(prev => {
      const next = new Set(prev);
      if (next.has(stepId)) next.delete(stepId);
      else next.add(stepId);
      return next;
    });
  }

  return (
    <div className="flex h-screen">
      <Sidebar mobileOpen={mobileOpen} onMobileClose={() => setMobileOpen(false)} />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Onboarding" onMenuClick={() => setMobileOpen(true)} />
        <main className="flex-1 overflow-y-auto p-4 sm:p-6 max-w-3xl mx-auto w-full">
          {/* progress banner */}
          <div className="card flex items-center gap-4 mb-5">
            <div className="size-12 rounded-full bg-accent-muted text-accent flex items-center justify-center font-semibold">
              {completed}/{total}
            </div>
            <div className="flex-1">
              <div className="text-sm font-medium">
                {completed === total ? 'You\'re all set 🎉' : `${total - completed} step${total - completed === 1 ? '' : 's'} to go`}
              </div>
              <div className="mt-1 h-1.5 rounded-full bg-ink-100 overflow-hidden">
                <div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} />
              </div>
            </div>
            {completed === total && (
              <Link href="/dashboard" className="btn-primary">
                Go to dashboard <ArrowRight size={14} />
              </Link>
            )}
          </div>

          {/* steps */}
          <ol className="space-y-3">
            {STEPS.map((step, i) => {
              const isDone = done.has(step.id);
              const isCurrent = i === current;
              const Icon = step.icon;
              return (
                <li
                  key={step.id}
                  className={`card transition-colors ${isCurrent ? 'border-accent' : ''}`}
                >
                  <div className="flex items-start gap-4">
                    <button
                      onClick={() => toggle(step.id)}
                      className={`mt-1 shrink-0 ${isDone ? 'text-emerald-600' : 'text-ink-300 hover:text-ink-700'}`}
                      aria-label={isDone ? 'Mark as not done' : 'Mark as done'}
                    >
                      {isDone ? <CheckCircle2 size={22} /> : <Circle size={22} />}
                    </button>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <Badge tone={isCurrent ? 'info' : 'default'}>Step {i + 1}</Badge>
                        <CardTitle className="!mb-0 flex items-center gap-2">
                          <Icon size={16} /> {step.title}
                        </CardTitle>
                      </div>
                      <CardDescription className="text-sm leading-relaxed">
                        {step.body}
                      </CardDescription>
                      {step.actions.length > 0 && (
                        <div className="mt-3 flex flex-wrap gap-2">
                          {step.actions.map(a => (
                            <Link
                              key={a.label} href={a.href}
                              className={a.primary ? 'btn btn-primary' : 'btn btn-outline'}
                              onClick={() => setCurrent(i)}
                            >
                              {a.label} <ArrowRight size={14} />
                            </Link>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>

          <div className="mt-6 text-center text-xs text-ink-500">
            For the long-form version, see <Link href="/audit" className="underline">docs/ONBOARDING.md</Link>.
          </div>
        </main>
      </div>
    </div>
  );
}
