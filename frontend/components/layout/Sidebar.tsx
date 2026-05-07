'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutDashboard, Workflow, Plug, Database, FileText, Settings,
  Zap, MessageSquare, BarChart3, Calendar, ScrollText, X, Sparkles,
  BookOpen,
  type LucideIcon,
} from 'lucide-react';
import { cn } from '@/lib/utils';

type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  group?: 'main' | 'help';
};

const items: NavItem[] = [
  { href: '/dashboard',  label: 'Dashboard',  icon: LayoutDashboard, group: 'main' },
  { href: '/workflows',  label: 'Workflows',  icon: Workflow,        group: 'main' },
  { href: '/platforms',  label: 'Platforms',  icon: Plug,            group: 'main' },
  { href: '/sources',    label: 'Sources',    icon: Database,        group: 'main' },
  { href: '/posts',      label: 'Posts',      icon: FileText,        group: 'main' },
  { href: '/reviews',    label: 'Reviews',    icon: MessageSquare,   group: 'main' },
  { href: '/triggers',   label: 'Triggers',   icon: Zap,             group: 'main' },
  { href: '/calendar',   label: 'Calendar',   icon: Calendar,        group: 'main' },
  { href: '/analytics',  label: 'Analytics',  icon: BarChart3,       group: 'main' },
  { href: '/audit',      label: 'Audit log',  icon: ScrollText,      group: 'main' },
  // Help section — pinned to the bottom of the nav
  { href: '/onboarding', label: 'Get started', icon: Sparkles,       group: 'help' },
  { href: '/help',       label: 'Help & docs', icon: BookOpen,       group: 'help' },
  { href: '/settings',   label: 'Settings',    icon: Settings,       group: 'help' },
];

export function Sidebar({ mobileOpen = false, onMobileClose }: {
  mobileOpen?: boolean;
  onMobileClose?: () => void;
}) {
  const pathname = usePathname();
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
        <div className="flex items-center justify-between mb-4 px-3 py-2">
          <Link href="/dashboard" className="flex items-center gap-2" onClick={onMobileClose}>
            <div className="size-7 rounded-lg bg-accent" />
            <span className="font-semibold tracking-tight">SMMS</span>
          </Link>
          {mobileOpen && (
            <button onClick={onMobileClose} className="lg:hidden text-ink-500">
              <X size={18} />
            </button>
          )}
        </div>
        <nav className="flex flex-col gap-0.5 flex-1">
          {items.filter(i => i.group !== 'help').map(({ href, label, icon: Icon }) => {
            const active = pathname?.startsWith(href);
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
                {label}
              </Link>
            );
          })}

          <div className="mt-4 mb-1 px-3 text-[10px] uppercase tracking-wider text-ink-400">
            Help
          </div>
          {items.filter(i => i.group === 'help').map(({ href, label, icon: Icon }) => {
            const active = pathname?.startsWith(href);
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
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto px-3 pt-4 text-xs text-ink-500">
          v0.1.0 · plug-and-play
        </div>
      </aside>
    </>
  );
}
