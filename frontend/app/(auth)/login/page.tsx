'use client';
import { signIn } from 'next-auth/react';
import { Button } from '@/components/ui/Button';

export default function LoginPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-ink-50">
      <div className="card w-full max-w-md p-8 text-center">
        <div className="mx-auto size-10 rounded-xl bg-accent mb-4" />
        <h1 className="text-xl font-semibold tracking-tight">Sign in to SMMS</h1>
        <p className="text-sm text-ink-500 mt-1">
          Use your organization's Okta account. Single sign-on is enforced.
        </p>
        <div className="mt-6">
          <Button className="w-full" onClick={() => signIn('okta', { callbackUrl: '/dashboard' })}>
            Continue with Okta
          </Button>
        </div>
        <p className="text-xs text-ink-500 mt-6">
          Trouble signing in? Contact your workspace admin.
        </p>
      </div>
    </div>
  );
}
