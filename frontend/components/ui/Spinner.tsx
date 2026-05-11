'use client';
/**
 * Spinner family — the unified loading visuals across the app.
 *
 * Three visual styles share one component because each fits a
 * different context:
 *
 *   * ``ring``  — the default. Conic-gradient arc that rotates. Most
 *                 readable at every size. Use everywhere unless you
 *                 have a reason to pick something else.
 *   * ``dots``  — three pulsing dots. Lighter visual weight; use
 *                 inline next to text where a spinning ring would
 *                 feel heavy ("Saving…", "Updating…").
 *   * ``bars``  — three vertical bars with a wave animation. Fits
 *                 alongside chart / analytics surfaces where the
 *                 visual language is already bar-based.
 *
 * All three are accent-coloured by default; pass ``className`` with
 * a ``text-`` utility to override.
 *
 * Accessibility: every spinner has ``role="status"`` and a
 * ``<span class="sr-only">`` label so screen readers announce
 * what's loading. Pass ``label`` to override the generic "Loading".
 *
 * Reduced-motion: when ``prefers-reduced-motion: reduce`` is set,
 * the rotation slows to 2.4s (still visible movement, but not
 * vestibular-trigger fast). We don't disable entirely because a
 * frozen spinner reads as "this hung".
 */
import { cn } from '@/lib/utils';

type Size = 'xs' | 'sm' | 'md' | 'lg';
type Variant = 'ring' | 'dots' | 'bars';

const SIZE_PX: Record<Size, number> = {
  xs: 12, sm: 16, md: 24, lg: 36,
};

export interface SpinnerProps {
  size?: Size;
  variant?: Variant;
  label?: string;
  className?: string;
}

export function Spinner({
  size = 'sm', variant = 'ring', label = 'Loading', className,
}: SpinnerProps) {
  const px = SIZE_PX[size];

  if (variant === 'dots') {
    const dot = Math.max(3, Math.round(px / 4));
    return (
      <span
        role="status"
        aria-label={label}
        className={cn('inline-flex items-center gap-1 text-accent', className)}
        style={{ height: px }}
      >
        {[0, 1, 2].map(i => (
          <span
            key={i}
            className="rounded-full bg-current animate-smms-dot"
            style={{
              width: dot, height: dot,
              animationDelay: `${i * 150}ms`,
            }}
          />
        ))}
        <span className="sr-only">{label}</span>
      </span>
    );
  }

  if (variant === 'bars') {
    const bar = Math.max(2, Math.round(px / 6));
    return (
      <span
        role="status"
        aria-label={label}
        className={cn('inline-flex items-end gap-0.5 text-accent', className)}
        style={{ height: px }}
      >
        {[0, 1, 2, 3].map(i => (
          <span
            key={i}
            className="bg-current rounded-sm animate-smms-bar"
            style={{
              width: bar,
              height: px,
              animationDelay: `${i * 120}ms`,
            }}
          />
        ))}
        <span className="sr-only">{label}</span>
      </span>
    );
  }

  // Default — conic-gradient ring. The mask cuts a transparent hole
  // in the middle so it works on any background; the conic gradient
  // makes the rotating arc feel more "alive" than a hard border.
  return (
    <span
      role="status"
      aria-label={label}
      className={cn('inline-block text-accent animate-spin-smms', className)}
      style={{
        width: px, height: px,
        borderRadius: '50%',
        background: 'conic-gradient(from 0deg, currentColor 0deg, transparent 270deg, currentColor 360deg)',
        WebkitMask: 'radial-gradient(circle, transparent calc(50% - 2px), #000 calc(50% - 2px))',
        mask: 'radial-gradient(circle, transparent calc(50% - 2px), #000 calc(50% - 2px))',
      }}
    >
      <span className="sr-only">{label}</span>
    </span>
  );
}


/**
 * InlineLoader — spinner + label, sits inline with text. Use for
 * row-level fetches, "Saving…" indicators, anything where you want
 * to tell the user what's happening alongside showing them that
 * something IS happening.
 */
export function InlineLoader({
  label, size = 'sm', variant = 'dots', className,
}: {
  label: string;
  size?: Size;
  variant?: Variant;
  className?: string;
}) {
  return (
    <span
      className={cn('inline-flex items-center gap-2 text-sm text-ink-600', className)}
    >
      <Spinner size={size} variant={variant} label={label} />
      <span aria-hidden>{label}</span>
    </span>
  );
}


/**
 * PageLoader — centred full-area loader. Use as the fallback for
 * Suspense boundaries or as the initial state of a page whose data
 * hasn't loaded yet. Always pair with a label that says what's
 * loading; "Loading…" alone is hostile UX.
 */
export function PageLoader({
  label = 'Loading',
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-3 py-16 text-ink-500',
        className,
      )}
    >
      <Spinner size="lg" variant="ring" label={label} />
      <p className="text-sm">{label}</p>
    </div>
  );
}
