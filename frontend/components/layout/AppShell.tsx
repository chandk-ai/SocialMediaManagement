'use client';

/**
 * AppShell — Sidebar + TopBar + scrollable main area.
 *
 * Most pages still wire their own `<Sidebar />` / `<TopBar />` for
 * historical reasons. New pages should use AppShell — one prop instead
 * of nine lines of layout boilerplate:
 *
 *     <AppShell>
 *       <p>Page content</p>
 *     </AppShell>
 *
 * Breadcrumbs come for free via TopBar (which reads the current path
 * and renders them). Pages with dynamic detail names pass
 * ``crumbOverrides`` so the last crumb says "Acme Newsletter" instead
 * of "abc-1234…".
 */
import { useState } from 'react';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';

export function AppShell({
  children,
  title,
  crumbOverrides,
}: {
  children: React.ReactNode;
  title?: string;
  crumbOverrides?: Record<string, string>;
}) {
  const [mobileOpen, setMobileOpen] = useState(false);
  return (
    <div className="flex h-screen">
      <Sidebar
        mobileOpen={mobileOpen}
        onMobileClose={() => setMobileOpen(false)}
      />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar
          title={title}
          crumbOverrides={crumbOverrides}
          onMenuClick={() => setMobileOpen(true)}
        />
        <main className="flex-1 overflow-y-auto">
          {children}
        </main>
      </div>
    </div>
  );
}
