'use client';
/**
 * Help & docs — comprehensive in-app documentation.
 *
 * Sidebar of topics on the left, scrollable content on the right. Sections:
 *   1. Getting started (5-min path to first published post)
 *   2. Connecting platforms (provider-by-provider walkthrough)
 *   3. Adding content sources (RSS, Notion, Drive, ...)
 *   4. Building workflows (templates, voice, schedule, validation)
 *   5. Reviews & approval flow (in-app + WhatsApp/Telegram)
 *   6. AI / LLM setup (which provider, where to get keys, costs)
 *   7. Calendar & scheduling (drag-to-reschedule, AI slots)
 *   8. Analytics & metrics
 *   9. Triggers (chat-driven posting)
 *  10. Troubleshooting
 *  11. FAQ
 *  12. Glossary
 *
 * URL hashes (#section-id) deep-link from anywhere in the app — e.g.
 * "Need help?" links on the Platforms page can point to /help#connecting-instagram.
 */
import * as React from 'react';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { Input } from '@/components/ui/Input';
import {
  Sparkles, Plug, Database, Workflow, MessageSquare, Brain, Calendar,
  BarChart3, Zap, AlertCircle, HelpCircle, BookOpen, Search, ChevronRight,
  CheckCircle2, ExternalLink, Info,
  type LucideIcon,
} from 'lucide-react';

type Topic = {
  id: string;
  title: string;
  icon: LucideIcon;
  group: 'start' | 'using' | 'help';
};

const TOPICS: Topic[] = [
  { id: 'getting-started',     title: 'Getting started',          icon: Sparkles,       group: 'start' },
  { id: 'concepts',            title: 'Core concepts',            icon: BookOpen,       group: 'start' },

  { id: 'connecting-platforms',title: 'Connecting platforms',     icon: Plug,           group: 'using' },
  { id: 'adding-sources',      title: 'Adding sources',           icon: Database,       group: 'using' },
  { id: 'building-workflows',  title: 'Building workflows',       icon: Workflow,       group: 'using' },
  { id: 'reviews-approval',    title: 'Reviews & approval',       icon: MessageSquare,  group: 'using' },
  { id: 'llm-setup',           title: 'AI / LLM setup',           icon: Brain,          group: 'using' },
  { id: 'calendar',            title: 'Calendar & scheduling',    icon: Calendar,       group: 'using' },
  { id: 'analytics',           title: 'Analytics',                icon: BarChart3,      group: 'using' },
  { id: 'triggers',            title: 'Chat triggers',            icon: Zap,            group: 'using' },

  { id: 'troubleshooting',     title: 'Troubleshooting',          icon: AlertCircle,    group: 'help' },
  { id: 'faq',                 title: 'FAQ',                      icon: HelpCircle,     group: 'help' },
  { id: 'glossary',            title: 'Glossary',                 icon: BookOpen,       group: 'help' },
];

const GROUP_LABELS: Record<string, string> = {
  start: 'Get going',
  using: 'How to use',
  help:  'Reference',
};

export default function HelpPage() {
  const [active, setActive] = useState<string>('getting-started');
  const [query, setQuery] = useState('');

  // On mount: jump to URL hash if present
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const h = window.location.hash.replace('#', '');
    if (h && TOPICS.find(t => t.id === h)) setActive(h);
  }, []);

  // Update URL hash when active changes
  useEffect(() => {
    if (typeof window !== 'undefined') {
      history.replaceState(null, '', `#${active}`);
    }
  }, [active]);

  const filtered = query
    ? TOPICS.filter(t => t.title.toLowerCase().includes(query.toLowerCase()))
    : TOPICS;

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Help & docs" />
        <main className="flex-1 overflow-hidden flex">
          {/* Topic sidebar */}
          <aside className="w-64 shrink-0 border-r border-ink-200 bg-white p-4 overflow-y-auto hidden md:block">
            <div className="relative mb-4">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-400" />
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search topics…"
                className="pl-8"
              />
            </div>
            {(['start', 'using', 'help'] as const).map(g => {
              const inGroup = filtered.filter(t => t.group === g);
              if (inGroup.length === 0) return null;
              return (
                <div key={g} className="mb-4">
                  <div className="text-[10px] uppercase tracking-wider text-ink-400 mb-1 px-2">
                    {GROUP_LABELS[g]}
                  </div>
                  <ul className="space-y-0.5">
                    {inGroup.map(t => {
                      const Icon = t.icon;
                      return (
                        <li key={t.id}>
                          <button
                            onClick={() => setActive(t.id)}
                            className={`w-full text-left flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm transition-colors ${
                              active === t.id
                                ? 'bg-accent-muted text-accent font-medium'
                                : 'text-ink-700 hover:bg-ink-100'
                            }`}
                          >
                            <Icon size={14} />
                            {t.title}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              );
            })}
          </aside>

          {/* Content area */}
          <article className="flex-1 overflow-y-auto p-6 md:p-10 max-w-4xl">
            {/* Mobile topic picker */}
            <select
              value={active}
              onChange={(e) => setActive(e.target.value)}
              className="input md:hidden mb-4"
            >
              {TOPICS.map(t => (
                <option key={t.id} value={t.id}>{t.title}</option>
              ))}
            </select>

            {active === 'getting-started'      && <GettingStarted />}
            {active === 'concepts'             && <CoreConcepts />}
            {active === 'connecting-platforms' && <ConnectingPlatforms />}
            {active === 'adding-sources'       && <AddingSources />}
            {active === 'building-workflows'   && <BuildingWorkflows />}
            {active === 'reviews-approval'     && <ReviewsApproval />}
            {active === 'llm-setup'            && <LlmSetup />}
            {active === 'calendar'             && <CalendarHelp />}
            {active === 'analytics'            && <AnalyticsHelp />}
            {active === 'triggers'             && <TriggersHelp />}
            {active === 'troubleshooting'      && <Troubleshooting />}
            {active === 'faq'                  && <Faq />}
            {active === 'glossary'             && <Glossary />}
          </article>
        </main>
      </div>
    </div>
  );
}

/* ───────── Reusable building blocks ─────────────────────────────────── */

function H1({ children }: { children: React.ReactNode }) {
  return <h1 className="text-2xl font-semibold tracking-tight text-ink-900 mb-2">{children}</h1>;
}

function H2({ children, id }: { children: React.ReactNode; id?: string }) {
  return (
    <h2 id={id} className="text-base font-semibold text-ink-900 mt-8 mb-2 scroll-mt-20">
      {children}
    </h2>
  );
}

function P({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-ink-700 leading-relaxed mb-3">{children}</p>;
}

function Lead({ children }: { children: React.ReactNode }) {
  return <p className="text-base text-ink-600 leading-relaxed mb-6">{children}</p>;
}

function Steps({ children }: { children: React.ReactNode }) {
  return (
    <ol className="list-decimal pl-5 text-sm text-ink-700 space-y-2 mb-4 marker:text-ink-400">
      {children}
    </ol>
  );
}

function Bullets({ children }: { children: React.ReactNode }) {
  return (
    <ul className="list-disc pl-5 text-sm text-ink-700 space-y-1.5 mb-4 marker:text-ink-400">
      {children}
    </ul>
  );
}

function Pre({ children }: { children: React.ReactNode }) {
  return (
    <pre className="bg-ink-50 border border-ink-100 rounded-lg p-3 text-xs font-mono whitespace-pre-wrap break-words mb-3">
      {children}
    </pre>
  );
}

function Tip({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900 my-4 flex items-start gap-2">
      <Info size={16} className="mt-0.5 shrink-0" />
      <div>{children}</div>
    </div>
  );
}

function Warn({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 my-4 flex items-start gap-2">
      <AlertCircle size={16} className="mt-0.5 shrink-0" />
      <div>{children}</div>
    </div>
  );
}

function Goto({ to, label }: { to: string; label: string }) {
  return (
    <Link href={to} className="text-accent inline-flex items-center gap-1 underline">
      {label} <ExternalLink size={12} />
    </Link>
  );
}

/* ───────── Section: Getting started ─────────────────────────────────── */

function GettingStarted() {
  return (
    <>
      <H1>Get going in 10 minutes</H1>
      <Lead>
        SMMS turns one piece of content into platform-tailored posts on every account
        you connect — drafted by AI, reviewed by you, scheduled or published automatically.
        Here's the shortest path to your first published post.
      </Lead>

      <Card className="mb-6">
        <CardTitle className="flex items-center gap-2"><CheckCircle2 size={16} /> 5-minute path</CardTitle>
        <Steps>
          <li>
            <strong>Add your AI key</strong> — open <Goto to="/settings" label="Settings" />,
            paste your Anthropic / OpenAI / Google key, set it as preferred. <em>Without this, drafts are placeholder text.</em>
          </li>
          <li>
            <strong>Connect one social account</strong> — open <Goto to="/platforms" label="Platforms" />,
            pick a provider (LinkedIn is easiest), authorize.
          </li>
          <li>
            <strong>Add one content source</strong> — open <Goto to="/sources" label="Sources" />,
            add an RSS feed (e.g. <code>https://news.ycombinator.com/rss</code>). Click <strong>Test connection</strong> before saving.
          </li>
          <li>
            <strong>Build a workflow</strong> — open <Goto to="/workflows" label="Workflows" />,
            click a template card. The wizard fills in everything; you only pick which source + which account.
          </li>
          <li>
            <strong>Run it once</strong> — back on the Workflows page, click <strong>Run now</strong> on your new card.
            The agents draft a post, you approve in <Goto to="/reviews" label="Reviews" />, it publishes.
          </li>
        </Steps>
      </Card>

      <Tip>
        New to this? Use the interactive 7-step checklist at <Goto to="/onboarding" label="Onboarding" /> —
        same content, but with progress tracking and deep-links into each section.
      </Tip>
    </>
  );
}

/* ───────── Section: Core concepts ───────────────────────────────────── */

function CoreConcepts() {
  return (
    <>
      <H1>Core concepts</H1>
      <Lead>Six entities make up the system. Once you understand these, everything else follows.</Lead>

      <H2 id="org">Organization</H2>
      <P>Your workspace. Holds your platforms, sources, workflows, posts, AI keys, and team members. All data is scoped to one org via row-level security.</P>

      <H2 id="platform">Platform / Account</H2>
      <P>
        A connected social account. <strong>Multiple accounts per provider</strong> are supported —
        e.g. two Instagram Business accounts under one Meta app, three LinkedIn pages, etc.
      </P>

      <H2 id="source">Source</H2>
      <P>
        Where the agents pull reference material from. RSS feeds, web pages, Notion databases,
        Google Drive folders, S3 buckets, GitHub repos, SQL databases, vector DBs, YouTube channels.
      </P>

      <H2 id="workflow">Workflow</H2>
      <P>
        The recipe: <em>this source → these agents → these accounts → this schedule</em>.
        Workflows can be manual, cron-scheduled, or chat-triggered.
      </P>

      <H2 id="agents">The 4 agents</H2>
      <Bullets>
        <li><strong>Planner</strong> — reads the directive + source items and decides which platforms to target with which angle.</li>
        <li><strong>Executor</strong> — drafts the actual post text per platform.</li>
        <li><strong>Evaluator</strong> — scores quality. If too low, loops back.</li>
        <li><strong>Critique</strong> — generates revision notes when quality is below threshold.</li>
      </Bullets>

      <H2 id="post">Post</H2>
      <P>
        One draft, on one account. Goes through:&nbsp;
        <Badge tone="default">draft</Badge> →{' '}
        <Badge tone="warning">review</Badge> →{' '}
        <Badge tone="default">approved</Badge> →{' '}
        <Badge tone="default">scheduled</Badge> →{' '}
        <Badge tone="success">published</Badge>{' '}
        (or <Badge tone="danger">failed</Badge>).
      </P>

      <H2 id="trigger">Trigger</H2>
      <P>
        How a workflow run starts. Manual button, cron schedule, webhook, or inbound chat
        message (WhatsApp / Telegram / Slack / Instagram DM). Chat triggers also handle the review.
      </P>
    </>
  );
}

/* ───────── Section: Connecting platforms ────────────────────────────── */

function ConnectingPlatforms() {
  return (
    <>
      <H1>Connecting platforms</H1>
      <Lead>
        Each provider has its own developer-portal setup. Here's the shortest path for the
        platforms most users start with.
      </Lead>

      <H2 id="connecting-meta">Facebook + Instagram + Threads (Meta)</H2>
      <P>One Meta app covers all three. The OAuth screen is served by Facebook even when you connect Instagram — that's by Meta's design, not a bug.</P>
      <Steps>
        <li>Go to <a className="underline" href="https://developers.facebook.com/apps/" target="_blank" rel="noreferrer">developers.facebook.com/apps</a> → Create app → use case "Other" → app type "Business".</li>
        <li>Add products: <strong>Instagram</strong>, <strong>Pages</strong>, <strong>Facebook Login for Business</strong>.</li>
        <li>Under <em>Facebook Login for Business → Settings</em>, paste this URL into <strong>Valid OAuth Redirect URIs</strong>: <code>https://[your-domain]/oauth/callback</code></li>
        <li>Under <em>App settings → Basic</em>, copy <strong>App ID</strong> and <strong>App Secret</strong>.</li>
        <li>On the backend (Render), set env vars: <code>META_CLIENT_ID</code>, <code>META_CLIENT_SECRET</code>.</li>
        <li>Open <Goto to="/platforms" label="Platforms" /> → click Connect on Instagram or Facebook.</li>
        <li>If you have multiple Pages, the Settings dialog on each tile lets you pick which Page this account publishes to.</li>
      </Steps>
      <Warn>
        <strong>Personal Facebook profiles can NOT be posted to.</strong> Meta deprecated that
        in 2018. Same for Facebook Groups (deprecated 2024). Only <strong>Pages</strong>
        (any category — including non-profit) are publishable.
      </Warn>

      <H2 id="connecting-linkedin">LinkedIn</H2>
      <Steps>
        <li>Go to <a className="underline" href="https://www.linkedin.com/developers/apps" target="_blank" rel="noreferrer">linkedin.com/developers/apps</a> → Create app.</li>
        <li>In <em>Auth</em> tab, add <code>https://[your-domain]/oauth/callback</code> as Authorized redirect URL.</li>
        <li>Request these scopes: <code>openid</code>, <code>profile</code>, <code>email</code>, <code>w_member_social</code>.</li>
        <li>Backend env vars: <code>LINKEDIN_CLIENT_ID</code>, <code>LINKEDIN_CLIENT_SECRET</code>.</li>
        <li>Click Connect on the LinkedIn tile in Platforms.</li>
      </Steps>

      <H2 id="connecting-x">X (Twitter)</H2>
      <Steps>
        <li>Go to <a className="underline" href="https://developer.x.com/en/portal/dashboard" target="_blank" rel="noreferrer">developer.x.com</a> and create a project + app.</li>
        <li>Enable User authentication settings → OAuth 2.0 → Public client.</li>
        <li>Callback URL: <code>https://[your-domain]/oauth/callback</code>.</li>
        <li>Required scopes: <code>tweet.read tweet.write users.read offline.access</code>.</li>
        <li>Backend env vars: <code>TWITTER_CLIENT_ID</code>, <code>TWITTER_CLIENT_SECRET</code>.</li>
      </Steps>

      <H2 id="connecting-others">YouTube, Telegram, Slack, Discord, Bluesky, Mastodon, Reddit, Pinterest, Tumblr</H2>
      <Bullets>
        <li><strong>YouTube</strong> — Google Cloud Console → APIs & Services → enable YouTube Data API v3 → OAuth credentials. Backend: <code>GOOGLE_CLIENT_ID/SECRET</code>.</li>
        <li><strong>Telegram</strong> — no OAuth. Talk to @BotFather, paste the bot token + chat id directly into the platform's config.</li>
        <li><strong>Slack / Discord</strong> — easiest is a webhook URL from the channel's integration settings; paste it in.</li>
        <li><strong>Bluesky</strong> — Settings → App Passwords → create one; paste handle + app password.</li>
        <li><strong>Mastodon</strong> — Preferences → Development → New application → use the access token.</li>
        <li><strong>Reddit</strong> — <a className="underline" href="https://www.reddit.com/prefs/apps" target="_blank" rel="noreferrer">reddit.com/prefs/apps</a> → create a "web app". Backend: <code>REDDIT_CLIENT_ID/SECRET</code>. Specify the subreddit in config.</li>
        <li><strong>Pinterest</strong> — developers.pinterest.com → app → connect. Pick the board id in config.</li>
        <li><strong>Tumblr</strong> — tumblr.com/oauth/apps → app → use OAuth. Specify the blog hostname.</li>
      </Bullets>
      <Tip>The <strong>Test publish</strong> button on each connected account sends a small "Hello from SMMS — please ignore" post end-to-end so you can verify connectivity before scheduling real content.</Tip>
    </>
  );
}

/* ───────── Section: Adding sources ──────────────────────────────────── */

function AddingSources() {
  return (
    <>
      <H1>Adding content sources</H1>
      <Lead>
        Sources feed your agents with reference material. Each source plugin has its own
        schema-driven form — no JSON to hand-edit.
      </Lead>

      <H2 id="source-rss">RSS / Atom feed (easiest)</H2>
      <Steps>
        <li>Open <Goto to="/sources" label="Sources" /> → choose "RSS / Atom feed" from the picker.</li>
        <li>Paste the feed URL (e.g. <code>https://news.ycombinator.com/rss</code>).</li>
        <li>Click <strong>Test connection</strong> — green check means it's reachable.</li>
        <li>Click <strong>Create source</strong>.</li>
      </Steps>

      <H2 id="source-web">Web page or web crawler</H2>
      <P>
        <strong>Web scraper</strong> reads a single page (e.g. a blog post). <strong>Web
        crawler</strong> recursively follows links from a seed URL (depth-limited). Use
        scraper for one-shot inputs, crawler when you want to ingest a whole site.
      </P>

      <H2 id="source-notion">Notion</H2>
      <Steps>
        <li>In Notion: Settings → Integrations → New internal integration → copy the secret.</li>
        <li>Share the database with your integration: open the DB → ⋯ → Add connections.</li>
        <li>Copy the database ID from the URL (32-char hex without dashes).</li>
        <li>In Sources, pick Notion → paste integration secret + database ID.</li>
      </Steps>
      <P>
        <strong>CMS mode (recommended for content teams):</strong> if you already
        manage your content calendar in Notion, toggle "Treat each row as a
        publishable post" on the Source. The system reads a <code>Status</code>{' '}
        select field, only publishes rows you've marked <code>Ready</code>,
        and writes back <code>Published</code> + the live URL when it's done.
        Failures land back on the row as <code>Failed</code> with the error
        text. No separate calendar to maintain.
      </P>
      <P>
        Database fields the integration looks for (names configurable per Source):
      </P>
      <Bullets>
        <li><strong>Name</strong> — title (built-in).</li>
        <li><strong>Content</strong> — rich text — the actual post body.</li>
        <li><strong>Status</strong> — select with values <code>Draft / Ready / Published / Failed</code>.</li>
        <li><strong>Platforms</strong> — multi-select; values match plugin names (<code>linkedin</code>, <code>twitter</code>, ...). Empty = all the workflow's platforms.</li>
        <li><strong>Scheduled at</strong> — date (optional, deferred-publish).</li>
        <li><strong>Published URL</strong> — url, written by us after publish.</li>
        <li><strong>Error</strong> — rich text, written on failure.</li>
      </Bullets>

      <H2 id="source-drive">Google Drive</H2>
      <Steps>
        <li>Google Cloud Console → IAM → Service Accounts → Create.</li>
        <li>Create a JSON key for the service account → download.</li>
        <li>Share your Drive folder with the service account's email.</li>
        <li>In Sources, pick Google Drive → paste the entire JSON key contents + folder ID.</li>
      </Steps>

      <H2 id="source-other">Other connectors</H2>
      <Bullets>
        <li><strong>S3 / MinIO / R2</strong> — bucket name + region + (optional) custom endpoint + credentials.</li>
        <li><strong>GitHub</strong> — repo path (<code>owner/name</code>) + optional PAT for private repos. Pick issues / releases / readme.</li>
        <li><strong>SQL database</strong> — SQLAlchemy URL (<code>postgresql+asyncpg://...</code>) + a SELECT query.</li>
        <li><strong>MongoDB</strong> — connection URI + database + collection. Map title/body field names.</li>
        <li><strong>Vector DB</strong> — Pinecone / Chroma / Qdrant / Weaviate. Top-k semantic search using a query string.</li>
        <li><strong>YouTube</strong> — channel ID (UC…) + Data API key. Pulls transcripts.</li>
      </Bullets>

      <Tip>
        Every source supports <strong>Test connection</strong> + <strong>Preview</strong> (5 most
        recent items). If a source fails repeatedly, the tile shows
        a red error count + the last error message inline.
      </Tip>
    </>
  );
}

/* ───────── Section: Building workflows ──────────────────────────────── */

function BuildingWorkflows() {
  return (
    <>
      <H1>Building workflows</H1>
      <Lead>
        A workflow is the smallest unit of automation: a source feeds the agents,
        the agents target accounts, posts go out on the schedule you pick.
      </Lead>

      <H2 id="wf-templates">Start from a template (recommended)</H2>
      <P>
        On the empty <Goto to="/workflows" label="Workflows" /> page, the four template cards
        prefill name, schedule, tone, and target platforms:
      </P>
      <Bullets>
        <li><strong>Daily blog → LinkedIn + Instagram</strong> — pulls each new RSS entry, drafts platform-specific copy, runs daily at 9am.</li>
        <li><strong>Weekly RSS digest</strong> — Monday morning round-up of the past week's items into one Facebook + LinkedIn post.</li>
        <li><strong>On-demand from chat</strong> — no schedule. You text "post about X" in WhatsApp/Telegram/Slack and the agents draft.</li>
        <li><strong>Drive doc → social</strong> — watches a Drive folder; each new doc → social drafts.</li>
      </Bullets>

      <H2 id="wf-wizard">The 5-step wizard</H2>
      <Steps>
        <li><strong>Template</strong> — pick or skip blank.</li>
        <li><strong>Sources</strong> — pick which (multiple OK; can be empty for chat-driven).</li>
        <li><strong>Accounts</strong> — pick at least one target. Grouped by provider.</li>
        <li><strong>Voice & schedule</strong> — name, tone, audience, LLM provider, "require human approval" toggle, schedule kind.</li>
        <li><strong>Review</strong> — summary screen. Click Create.</li>
      </Steps>

      <H2 id="wf-schedule">Schedules</H2>
      <Bullets>
        <li><strong>Manual</strong> — only runs when you click Run now or a trigger fires.</li>
        <li><strong>Cron</strong> — standard 5-field expression. Examples: <code>0 9 * * *</code> = every day 9am, <code>0 8 * * 1</code> = every Monday 8am.</li>
        <li><strong>Interval</strong> — every N minutes. Useful for fast-moving sources.</li>
        <li><strong>Once</strong> — fires at a specific timestamp.</li>
      </Bullets>

      <H2 id="wf-edit">Editing later</H2>
      <P>
        Click <strong>Edit</strong> on any workflow card to change sources, target accounts,
        schedule, or voice. Click <strong>Details</strong> to see the agent trace per run +
        post history.
      </P>
    </>
  );
}

/* ───────── Section: Reviews ─────────────────────────────────────────── */

function ReviewsApproval() {
  return (
    <>
      <H1>Reviews & approval</H1>
      <Lead>
        Drafts that need human approval pause and wait. You decide: Approve / Revise (with
        feedback to the agents) / Reject. Decisions sync back to the same channel that
        triggered the review.
      </Lead>

      <H2 id="rv-where">Where reviews land</H2>
      <Bullets>
        <li><strong>In-app</strong> — workflows with no review channel → drafts show on <Goto to="/reviews" label="Reviews" /> as cards.</li>
        <li><strong>WhatsApp</strong> — agent sends the draft to your WhatsApp; reply "approve" / "revise: …" / "reject" to decide.</li>
        <li><strong>Telegram / Slack / Email</strong> — same idea, different transport.</li>
      </Bullets>

      <H2 id="rv-revise">"Revise with feedback" loop</H2>
      <P>
        Type a sentence of feedback (e.g. "make it more casual; cut to 2 sentences"). The
        agents re-run executor + evaluator + critique with your note as a critique input,
        bump the revision counter, and pause again for review. Up to <em>max_revisions</em>
        rounds (default 3, configurable per workflow).
      </P>
    </>
  );
}

/* ───────── Section: LLM setup ───────────────────────────────────────── */

function LlmSetup() {
  return (
    <>
      <H1>AI / LLM setup</H1>
      <Lead>
        Until you add a real API key, drafts fall back to a mock provider that just echoes
        the prompt — fine for testing the UI, useless for actual content.
      </Lead>

      <H2 id="llm-where">Where to add keys</H2>
      <P>
        Open <Goto to="/settings" label="Settings" /> → <em>AI / LLM providers</em> section.
        Each row has show/hide password toggle, save/update/remove buttons. Last 4
        characters of the key are shown after saving so you can identify which key is which.
      </P>

      <H2 id="llm-which">Which provider should I pick?</H2>
      <Bullets>
        <li><strong>Anthropic Claude</strong> (recommended for tone / nuance) — get key at <a className="underline" href="https://console.anthropic.com" target="_blank" rel="noreferrer">console.anthropic.com</a>.</li>
        <li><strong>OpenAI GPT-4</strong> (highest reasoning) — <a className="underline" href="https://platform.openai.com" target="_blank" rel="noreferrer">platform.openai.com</a>.</li>
        <li><strong>Google Gemini</strong> (fast + cheap) — <a className="underline" href="https://aistudio.google.com" target="_blank" rel="noreferrer">aistudio.google.com</a>.</li>
        <li><strong>Groq</strong> (lowest latency, hosts Llama) — <a className="underline" href="https://console.groq.com" target="_blank" rel="noreferrer">console.groq.com</a>.</li>
        <li><strong>Ollama</strong> (local, free, slower) — runs on your machine; no key needed.</li>
      </Bullets>

      <H2 id="llm-default">Default for new workflows</H2>
      <P>
        After saving keys, set a <strong>default provider + model</strong> at the top of the
        section. New workflows pick this up automatically; you can override per workflow if
        needed.
      </P>

      <Tip>
        Keys are encrypted at rest using the same TokenVault that holds OAuth tokens.
        The backend never returns plaintext keys to the frontend — only "is set" + last 4 chars.
      </Tip>

      <H2 id="llm-cost">Cost guardrails</H2>
      <P>
        Set a <strong>monthly LLM budget</strong> in Settings. The system tracks token usage
        per workflow run and stops creating new runs when the budget is exhausted (currently
        a soft limit; hard enforcement is planned for the next production-readiness pass).
      </P>
    </>
  );
}

/* ───────── Section: Calendar ────────────────────────────────────────── */

function CalendarHelp() {
  return (
    <>
      <H1>Calendar & scheduling</H1>
      <Lead>
        The <Goto to="/calendar" label="Calendar" /> page is the month-grid view of every
        scheduled / published / failed post.
      </Lead>

      <H2 id="cal-drag">Drag to reschedule</H2>
      <P>
        Grab any chip and drop it on a different day. The post's <code>scheduled_for</code>
        updates immediately (defaults to 9am on the new day; click the chip to fine-tune the time).
      </P>

      <H2 id="cal-ai-slots">AI-suggested slots</H2>
      <P>
        With "AI slots" toggled on, days where you've historically published &ge; 2 times are
        outlined in purple with a Sparkles icon. The heuristic is intentionally simple
        (client-side, no LLM call) — uses your past publishing pattern as a hint for when
        your audience tends to engage.
      </P>

      <H2 id="cal-filter">Filtering</H2>
      <P>
        Pick a provider from the dropdown to scope the calendar to one platform's posts —
        useful when you have lots of accounts.
      </P>
    </>
  );
}

/* ───────── Section: Analytics ───────────────────────────────────────── */

function AnalyticsHelp() {
  return (
    <>
      <H1>Analytics</H1>
      <Lead>
        <Goto to="/analytics" label="Analytics" /> has 4 sections, top to bottom.
      </Lead>

      <H2 id="an-kpis">Hero KPIs</H2>
      <Bullets>
        <li><strong>Published this week</strong> with week-over-week delta.</li>
        <li><strong>Scheduled</strong> count.</li>
        <li><strong>Awaiting review</strong> with deep-link to Reviews.</li>
        <li><strong>Failed (last 7 days)</strong> with deep-link to filtered Posts.</li>
      </Bullets>

      <H2 id="an-funnel">Pipeline funnel</H2>
      <P>
        Visualizes how drafts flow: Draft → Review → Approved → Published vs. Failed. Three
        conversion percentages: % reaching review, % reaching publish, % failure rate.
      </P>

      <H2 id="an-account">Per-account breakdown</H2>
      <P>
        Stacked bar per connected account showing published (green) / in-review (amber) /
        failed (red). Quickly identifies which accounts are trouble.
      </P>

      <H2 id="an-throughput">Daily throughput</H2>
      <P>14-day bar chart of posts created per day.</P>
    </>
  );
}

/* ───────── Section: Triggers ────────────────────────────────────────── */

function TriggersHelp() {
  return (
    <>
      <H1>Chat triggers</H1>
      <Lead>
        Triggers turn an inbound message ("post about today's food drive at 6pm") into a
        workflow run. Same channel handles the review — no app-switching.
      </Lead>

      <H2 id="trig-channels">Supported channels</H2>
      <Bullets>
        <li><strong>WhatsApp</strong> — Twilio Business API or Meta Cloud API.</li>
        <li><strong>Telegram</strong> — bot token + your chat id.</li>
        <li><strong>Slack</strong> — bot token + channel.</li>
        <li><strong>Email</strong> — inbound webhook (Mailgun / Postmark).</li>
        <li><strong>Instagram DM</strong> — via the Meta Messenger Platform.</li>
      </Bullets>

      <H2 id="trig-directives">What to type</H2>
      <Bullets>
        <li><em>"Post about Q4 launch on every Instagram and Facebook account"</em> — fan out by plugin.</li>
        <li><em>"Post about food drive today at 6pm to all accounts tagged 'community'"</em> — target by tag.</li>
        <li><em>"Schedule for tomorrow 9am: Q4 launch announcement"</em> — defer with directive.</li>
      </Bullets>
    </>
  );
}

/* ───────── Section: Troubleshooting ─────────────────────────────────── */

function Troubleshooting() {
  return (
    <>
      <H1>Troubleshooting</H1>
      <Lead>Most common issues and what to do about them.</Lead>

      <H2 id="ts-oauth-blocked">"URL Blocked" during OAuth</H2>
      <P>
        Meta / LinkedIn / X reject the redirect because the URL isn't whitelisted in their
        developer portal. Check the redirect URI is <em>exactly</em> what you registered —
        no trailing slash, https not http, no query string.
      </P>

      <H2 id="ts-tenant-not-found">"Tenant or user not found" (Postgres)</H2>
      <P>Supabase pooler URL has the wrong format. The username must be <code>postgres.&lt;project-ref&gt;</code>, not bare <code>postgres</code>. Use the URL shown in Supabase Dashboard → Connect → Transaction pooler → IPv4 toggle on.</P>

      <H2 id="ts-experimental">"Publishing not yet wired" / posts marked failed</H2>
      <P>The platform is still marked <Badge tone="warning">preview</Badge>. Currently TikTok and Medium are the only experimental ones. All others (FB, IG, LinkedIn, X, YouTube, Threads, Telegram, Slack, Discord, Bluesky, Mastodon, Reddit, Pinterest, Tumblr) publish for real.</P>

      <H2 id="ts-mock-content">Posts contain "[draft pending real LLM output]"</H2>
      <P>You're using the mock LLM. Add a real API key in <Goto to="/settings" label="Settings" /> and set it as preferred.</P>

      <H2 id="ts-source-failing">Source has a red "N errors" badge</H2>
      <P>Click the source tile → click <strong>Test</strong>. The error message is shown inline. Common causes: wrong URL, expired API key, rate limit, network issue.</P>

      <H2 id="ts-render-asleep">Backend slow or 502 on first load</H2>
      <P>Render's free/starter plan sleeps the service after idle. First request takes ~30s to wake up. Subsequent requests are fast. Upgrade to a paid plan for instant cold-start.</P>
    </>
  );
}

/* ───────── Section: FAQ ─────────────────────────────────────────────── */

function Faq() {
  const faqs: { q: string; a: React.ReactNode }[] = [
    {
      q: 'Can I post to my personal Facebook profile?',
      a: 'No. Meta deprecated personal-profile posting in 2018. Only Pages can be posted to via API — this includes non-profit Pages, business Pages, brand Pages, etc.',
    },
    {
      q: 'Can I post to Facebook Groups?',
      a: 'No. Meta deprecated the Groups API in April 2024.',
    },
    {
      q: 'How are my API keys protected?',
      a: 'AES-GCM encrypted at rest in Postgres using a master key on the backend. Never returned in plaintext to the frontend. Same vault used for OAuth tokens.',
    },
    {
      q: 'Can I have multiple accounts on the same platform?',
      a: 'Yes — that\'s a first-class feature. Click "Add another" on any provider card. Each account gets its own row, can be tagged, can be set as default for that provider.',
    },
    {
      q: 'How does scheduling work?',
      a: 'Cron and interval schedules are dispatched by Celery Beat (a worker that wakes up every minute and checks who\'s due). "Once" schedules fire at the specific timestamp. Manual workflows only run when you click Run now or a trigger fires.',
    },
    {
      q: 'Can I integrate my own LLM that\'s not in the list?',
      a: 'Yes — write a small adapter in backend/app/adapters/llm/ that implements LLMProvider and register it. The plugin system handles the rest.',
    },
    {
      q: 'Where do scheduled posts live?',
      a: 'In the calendar (month grid) and Posts page (filterable list). Scheduled posts are dispatched by Celery at their scheduled_for time.',
    },
    {
      q: 'How does the agent loop know when to stop revising?',
      a: 'Either when the Evaluator score crosses quality_threshold (default 0.75), max_revisions is hit (default 3), or human approval is required.',
    },
  ];
  return (
    <>
      <H1>FAQ</H1>
      <div className="space-y-4">
        {faqs.map((f, i) => (
          <details key={i} className="rounded-xl border border-ink-200 p-4 bg-white">
            <summary className="cursor-pointer text-sm font-medium text-ink-900">{f.q}</summary>
            <div className="text-sm text-ink-700 mt-2 leading-relaxed">{f.a}</div>
          </details>
        ))}
      </div>
    </>
  );
}

/* ───────── Section: Glossary ────────────────────────────────────────── */

function Glossary() {
  const terms: { t: string; d: React.ReactNode }[] = [
    { t: 'Agent', d: 'One of the four AI roles in the orchestrator: Planner, Executor, Evaluator, Critique.' },
    { t: 'Capability', d: 'A boolean flag on a platform plugin (text_only, image, video, threads, scheduling, analytics, experimental).' },
    { t: 'Connector', d: 'Synonym for plugin — a platform / source / LLM / trigger / review-channel adapter.' },
    { t: 'Directive', d: 'Free-text instruction passed to a workflow (e.g. "post about today\'s food drive"). Comes from chat triggers or the manual Run dialog.' },
    { t: 'DLQ (Dead-Letter Queue)', d: 'Where failed publish jobs land after retries are exhausted. Visible in the Posts page filtered by failed.' },
    { t: 'Org', d: 'Organization. The top-level tenant boundary. All your data is scoped here.' },
    { t: 'Pooler', d: 'Supabase\'s connection pooler (Supavisor / pgbouncer). Required for serverless/IPv4-only hosts like Render.' },
    { t: 'Run', d: 'One execution of a workflow. Has a status, agent trace, and produces zero or more posts.' },
    { t: 'Selector / TargetSelector', d: 'Rule that picks which connected accounts to publish to (by plugin, tag, handle, explicit ID).' },
    { t: 'Trace', d: 'The chronological log of agent + system events for one run. Shown on the workflow detail page.' },
  ];
  return (
    <>
      <H1>Glossary</H1>
      <dl className="grid grid-cols-1 md:grid-cols-[180px_1fr] gap-y-3 gap-x-4 text-sm">
        {terms.map((x, i) => (
          <React.Fragment key={i}>
            <dt className="font-medium text-ink-900">{x.t}</dt>
            <dd className="text-ink-700 leading-relaxed">{x.d}</dd>
          </React.Fragment>
        ))}
      </dl>
    </>
  );
}

