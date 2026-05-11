'use client';
/**
 * Sidebar nav, ordered by the user journey rather than alphabetically.
 *
 *   Dashboard           ← entry point / overview
 *   ── SETUP ──         (configure once)
 *     Sources           — where content comes FROM
 *     Platforms         — where content goes TO
 *     Workflows         — the recipe tying them together
 *     Triggers          — how workflows kick off
 *   ── OPERATE ──       (day-to-day)
 *     Calendar          — scheduled / planned posts
 *     Posts             — everything that's been drafted / published
 *     Reviews           — approval queue (subset of posts that need you)
 *   ── INSIGHTS ──      (look back)
 *     Analytics         — performance dashboards
 *     Audit log         — compliance / debug timeline
 *   ── HELP ──          (admin + reference, pinned at the bottom)
 *     Get started
 *     Help & docs
 *     Team
 *     Settings
 *
 * The cause-effect arrow runs top-to-bottom: inputs become outputs become
 * insight. Group headers are tiny uppercase ledes — visual rhythm without
 * adding clutter.
 */
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutDashboard, Workflow, Plug, Database, FileText, Settings,
  Zap, MessageSquare, BarChart3, Calendar, ScrollText, X, Sparkles,
  BookOpen, Users, Activity, Package, TrendingUp,
  type LucideIcon,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { Wordmark } from '@/components/brand/Logo';

type NavGroup = 'top' | 'setup' | 'operate' | 'insights' | 'admin' | 'help';

type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  group: NavGroup;
};

const ITEMS: NavItem[] = [
  // Always-on entry
  { href: '/dashboard',     label: 'Dashboard',   icon: LayoutDashboard, group: 'top' },

  // ── Setup (configure once) ──
  { href: '/sources',       label: 'Sources',     icon: Database,        group: 'setup' },
  { href: '/platforms',     label: 'Platforms',   icon: Plug,            group: 'setup' },
  { href: '/workflows',     label: 'Workflows',   icon: Workflow,        group: 'setup' },
  { href: '/triggers',      label: 'Triggers',    icon: Zap,             group: 'setup' },

  // ── Operate (day-to-day) ──
  { href: '/calendar',      label: 'Calendar',    icon: Calendar,        group: 'operate' },
  { href: '/posts',         label: 'Posts',       icon: FileText,        group: 'operate' },
  { href: '/reviews',       label: 'Reviews',     icon: MessageSquare,   group: 'operate' },

  // ── Insights (look back) ──
  { href: '/analytics',            label: 'Analytics',   icon: BarChart3,  group: 'insights' },
  { href: '/analytics/engagement', label: 'Engagement',  icon: TrendingUp, group: 'insights' },
  { href: '/knowledge',            label: 'Knowledge',   icon: BookOpen,   group: 'insights' },
  { href: '/audit',                label: 'Audit log',   icon: ScrollText, group: 'insights' },

  // ── Admin (operator-only — durable engine + plugin marketplace) ──
  { href: '/admin/jobs',    label: 'Jobs',        icon: Activity,        group: 'admin' },
  { href: '/admin/plugins', label: 'Plugins',     icon: Package,         group: 'admin' },

  // ── Help (admin + reference) ──
  { href: '/onboarding',    label: 'Get started', icon: Sparkles,        group: 'help' },
  { href: '/help',          label: 'Help & docs', icon: BookOpen,        group: 'help' },
  { href: '/settings/team', label: 'Team',        icon: Users,           group: 'help' },
  { href: '/settings',      label: 'Settings',    icon: Settings,        group: 'help' },
];

const GROUP_LABELS: Record<NavGroup, string | null> = {
  top:      null,             // no header for the dashboard row
  setup:    'Setup',
  operate:  'Operate',
  insights: 'Insights',
  admin:    'Admin',
  help:     'Help',
};

const GROUP_ORDER: NavGroup[] = ['top', 'setup', 'operate', 'insights', 'admin', 'help'];

export function Sidebar({ mobileOpen = false, onMobileClose }: {
  mobileOpen?: boolean;
  onMobileClose?: () => void;
}) {
  const pathname = usePathname();
  // Match exact path or proper sub-path (with segment boundary). Plain
  // startsWith would mark `/settings` active when we're on `/settings/team`.
  const isActive = (href: string) =>
    !!pathname && (pathname === href || pathname.startsWith(href + '/'));

  return (
    <>
      {/* mobile backdrop */}
      {mobileOpen && (
        <button
          aria-label="Close menu"
          onClick={onMobileClose}
          className="lg:hidden fixed inset-0 z-40 bg-black/40"
        />
      )}
      <aside className={cn(
        'w-60 shrink-0 border-r border-ink-200 bg-white px-3 py-4 flex-col z-50',
        // mobile: drawer
        'fixed inset-y-0 left-0 transition-transform lg:static lg:flex',
        mobileOpen ? 'flex translate-x-0' : 'hidden -translate-x-full lg:translate-x-0',
      )}>
        <div className="flex items-center justify-between mb-5 px-2 py-1">
          <Link
            href="/dashboard"
            onClick={onMobileClose}
            className="rounded-lg -mx-1 px-1 py-1 hover:bg-ink-100 transition-colors"
            aria-label="SMMS — go to dashboard"
          >
            <Wordmark variant="full" />
          </Link>
          {mobileOpen && (
            <button onClick={onMobileClose} className="lg:hidden text-ink-500">
              <X size={18} />
            </button>
          )}
        </div>

        <nav className="flex flex-col gap-0.5 flex-1">
          {GROUP_ORDER.map((group, gIdx) => {
            const groupItems = ITEMS.filter(i => i.group === group);
            if (groupItems.length === 0) return null;
            const label = GROUP_LABELS[group];
            return (
              <div key={group}>
                {label && (
                  <div className={cn(
                    'px-3 text-[10px] uppercase tracking-wider text-ink-400',
                    // First label gets a touch less top space; subsequent
                    // ones get more so the eye perceives a clear break.
                    gIdx === 1 ? 'mt-2 mb-1' : 'mt-4 mb-1',
                  )}>
                    {label}
                  </div>
                )}
                {groupItems.map(({ href, label: itemLabel, icon: Icon }) => {
                  const active = isActive(href);
                  return (
                    <Link
                      key={href} href={href}
                      onClick={onMobileClose}
                      className={cn(
                        'flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors',
                        active
                          ? 'bg-accent-muted text-accent font-medium'
                          : 'text-ink-700 hover:bg-ink-100'
                      )}
                    >
                      <Icon size={16} />
                      {itemLabel}
                    </Link>
                  );
                })}
              </div>
            );
          })}
        </nav>

        <div className="mt-auto px-3 pt-4 border-t border-ink-100 text-[10px] uppercase tracking-[0.14em] text-ink-400 font-medium">
          <span className="text-ink-500">v0.1.0</span>
          <span className="mx-1.5 text-ink-300">·</span>
          plug-and-play
        </div>
      </aside>
    </>
  );
}
