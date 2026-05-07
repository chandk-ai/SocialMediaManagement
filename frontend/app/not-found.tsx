import Link from 'next/link';
import { Compass } from 'lucide-react';

export default function NotFound() {
  return (
    <div className="min-h-screen flex items-center justify-center p-8">
      <div className="card max-w-md text-center py-10">
        <div className="mx-auto mb-3 size-10 rounded-full bg-accent-muted flex items-center justify-center text-accent">
          <Compass size={20} />
        </div>
        <h1 className="text-base font-semibold mb-1">Page not found</h1>
        <p className="text-sm text-ink-500 mb-5">
          The page you're looking for doesn't exist or has been moved.
        </p>
        <Link href="/dashboard" className="btn btn-primary">Back to dashboard</Link>
      </div>
    </div>
  );
}
