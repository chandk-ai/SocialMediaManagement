'use client';

/**
 * Breadcrumbs — auto-derives from the current pathname.
 *
 * Why this exists
 * ───────────────
 * Most pages used to render a single `<TopBar title="Sources" />`. That's
 * fine at depth 1, but a user three levels deep on
 *   /sources/abc-123/items
 * had no idea where they were or how to get back. Breadcrumbs solve both
 * problems: the current location is the last crumb, and every parent
 * level is a clickable link.
 *
 * How segments turn into labels
 * ─────────────────────────────
 * * Known segments are rebadged via SEGMENT_LABELS so e.g. /admin/jobs
 *   reads "Admin / Jobs" instead of "admin / jobs".
 * * UUID-shaped segments are abbreviated to `abc-123…` so the bar
 *   doesn't blow out on detail pages.
 * * Pages with richer context (the actual source name, the workflow's
 *   display name, etc.) can override any crumb via the `overrides`
 *   prop:
 *
 *       <Breadcrumbs overrides={{ [id]: source.name }} />
 *
 *   Keyed by the raw URL segment, value is the friendly label.
 */
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { ChevronRight, Home } from 'lucide-react';
import { cn } from '@/lib/utils';

// Segment → friendly label. Keeps URLs lowercase + URL-safe while the
// nav reads like a sentence the user might say out loud.
const SEGMENT_LABELS: Record<string, string> = {
  // Top-level
  dashboard: 'Dashboard',
  sources: 'Sources',
  platforms: 'Platforms',
  workflows: 'Workflows',
  posts: 'Posts',
  triggers: 'Triggers',
  reviews: 'Reviews',
  calendar: 'Calendar',
  analytics: 'Analytics',
  audit: 'Audit log',
  knowledge: 'Knowledge base',
  onboarding: 'Get started',
  help: 'Help & docs',
  settings: 'Settings',
  team: 'Team',
  // Admin (Pillar 6 / Pillar 1)
  admin: 'Admin',
  jobs: 'Jobs',
  plugins: 'Plugins',
  // Insights drill-downs
  engagement: 'Engagement',
  // Detail-page sub-routes
  items: 'Items',
  runs: 'Runs',
  edit: 'Edit',
  new: 'New',
};

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type Crumb = { href: string; label: string };

function deriveCrumbs(
  pathname: string, overrides: Record<string, string>,
): Crumb[] {
  const parts = pathname.split('/').filter(Boolean);
  const crumbs: Crumb[] = [];
  let acc = '';
  for (const seg of parts) {
    acc += '/' + seg;
    const override = overrides[seg];
    let label: string;
    if (override) {
      label = override;
    } else if (SEGMENT_LABELS[seg]) {
      label = SEGMENT_LABELS[seg];
    } else if (UUID_RE.test(seg)) {
      label = seg.slice(0, 8) + '…';
    } else {
      // Title-case unknown segments — covers ad-hoc nested routes
      // that haven't been registered in SEGMENT_LABELS yet.
      label = seg.replace(/[-_]/g, ' ').replace(
        /\b\w/g, (c) => c.toUpperCase(),
      );
    }
    crumbs.push({ href: acc, label });
  }
  return crumbs;
}

export function Breadcrumbs({
  overrides = {},
  className,
}: {
  overrides?: Record<string, string>;
  className?: string;
}) {
  const pathname = usePathname() || '/';
  const crumbs = deriveCrumbs(pathname, overrides);

  if (crumbs.length === 0) {
    // Dashboard root — nothing to show.
    return null;
  }

  return (
    <nav
      aria-label="Breadcrumb"
      className={cn(
        'flex items-center text-sm text-ink-600 min-w-0',
        className,
      )}
    >
      {/* Home — icon-only on desktop (the Sidebar already has "Dashboard"
          right there), icon + "Home" text on mobile where the sidebar is
          hidden behind a hamburger and the breadcrumb is the only
          persistent path back. */}
      <Link
        href="/dashboard"
        className="flex items-center gap-1 text-ink-500 hover:text-ink-800 shrink-0"
        aria-label="Home"
      >
        <Home size={14} />
        <span className="lg:hidden text-xs">Home</span>
      </Link>
      {crumbs.map((c, i) => {
        const isLast = i === crumbs.length - 1;
        return (
          <span key={c.href} className="flex items-center min-w-0">
            <ChevronRight size={14} className="mx-1.5 text-ink-300 shrink-0" />
            {isLast ? (
              <span
                aria-current="page"
                className="font-medium text-ink-900 truncate"
                title={c.label}
              >
                {c.label}
              </span>
            ) : (
              <Link
                href={c.href}
                className="text-ink-600 hover:text-ink-900 truncate"
                title={c.label}
              >
                {c.label}
              </Link>
            )}
          </span>
        );
      })}
    </nav>
  );
}
