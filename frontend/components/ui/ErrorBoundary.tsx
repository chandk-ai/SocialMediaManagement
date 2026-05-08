'use client';
import { Component, ErrorInfo, ReactNode } from 'react';
import { Button } from './Button';
import { AlertTriangle } from 'lucide-react';

type State = { hasError: boolean; message?: string };

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, message: error.message };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('UI ErrorBoundary caught:', error, info);
    // Forward to Sentry. We dynamic-import so this file still works in the
    // (uncommon) build configurations where @sentry/nextjs isn't available —
    // in that case console.error above is the only surface.
    void import('@sentry/nextjs')
      .then((Sentry) => {
        try {
          Sentry.withScope((scope) => {
            scope.setExtras({ componentStack: info?.componentStack });
            Sentry.captureException(error);
          });
        } catch {
          /* swallow — observability must never crash the app */
        }
      })
      .catch(() => {
        /* package not installed — ignore */
      });
  }

  reset = () => this.setState({ hasError: false, message: undefined });

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <div className="min-h-screen flex items-center justify-center p-8">
        <div className="card max-w-md text-center">
          <div className="mx-auto mb-3 size-10 rounded-full bg-red-50 flex items-center justify-center text-red-600">
            <AlertTriangle size={20} />
          </div>
          <h1 className="text-base font-semibold mb-1">Something went wrong</h1>
          <p className="text-sm text-ink-500 mb-4">
            {this.state.message ?? 'An unexpected error occurred.'}
          </p>
          <div className="flex justify-center gap-2">
            <Button variant="outline" onClick={() => location.reload()}>Reload</Button>
            <Button onClick={this.reset}>Try again</Button>
          </div>
        </div>
      </div>
    );
  }
}
