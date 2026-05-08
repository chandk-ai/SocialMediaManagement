'use client';
import { SessionProvider } from 'next-auth/react';
import { ToastProvider } from './ui/Toast';
import { ErrorBoundary } from './ui/ErrorBoundary';
import { SentryUserTagger } from './observability/SentryUserTagger';

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <ErrorBoundary>
        <SentryUserTagger />
        <ToastProvider>{children}</ToastProvider>
      </ErrorBoundary>
    </SessionProvider>
  );
}
