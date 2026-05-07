import { cn } from '@/lib/utils';

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        'animate-pulse rounded-md bg-ink-200/60',
        className,
      )}
      aria-hidden
    />
  );
}

export function CardSkeleton() {
  return (
    <div className="card p-5">
      <Skeleton className="h-4 w-1/3 mb-3" />
      <Skeleton className="h-3 w-2/3 mb-2" />
      <Skeleton className="h-3 w-1/2" />
    </div>
  );
}

export function ListSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="card flex items-center gap-4">
          <Skeleton className="h-10 w-10 rounded-full" />
          <div className="flex-1">
            <Skeleton className="h-3 w-1/3 mb-2" />
            <Skeleton className="h-3 w-2/3" />
          </div>
        </div>
      ))}
    </div>
  );
}
