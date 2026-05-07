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
    // Wire to Sentry / Honeycomb here.
    console.error('UI ErrorBoundary caught:', error, info);
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
