'use client';
import { SessionProvider } from 'next-auth/react';
import { ToastProvider } from './ui/Toast';
import { ErrorBoundary } from './ui/ErrorBoundary';

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <ErrorBoundary>
        <ToastProvider>{children}</ToastProvider>
      </ErrorBoundary>
    </SessionProvider>
  );
}
