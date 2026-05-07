'use client';
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';

export default function SettingsPage() {
  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Settings" />
        <main className="flex-1 overflow-y-auto p-6 space-y-4 max-w-2xl">
          <Card>
            <CardTitle>Organization</CardTitle>
            <CardDescription>Basic information about your workspace</CardDescription>
            <div className="mt-4 space-y-3">
              <Input placeholder="Organization name" defaultValue="Acme Corp" />
              <Input placeholder="Slug" defaultValue="acme" />
              <Button>Save</Button>
            </div>
          </Card>

          <Card>
            <CardTitle>LLM budget</CardTitle>
            <CardDescription>Monthly cap across all workflows</CardDescription>
            <div className="mt-4 flex items-end gap-3">
              <Input type="number" defaultValue={100} className="w-40" />
              <span className="text-sm text-ink-500 mb-2">USD / month</span>
            </div>
          </Card>

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
