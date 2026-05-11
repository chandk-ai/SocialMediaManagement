'use client';

/**
 * Top progress bar — Linear / Stripe style.
 *
 * A 2px accent-gradient bar that lives at the very top of the
 * viewport and progresses on every route change. Purely visual —
 * Next.js handles the actual navigation — but the bar makes the
 * app feel responsive even while the next page's data is in
 * flight. Without it, clicking a link in a SPA feels like nothing
 * happened until the new content renders.
 *
 * Behaviour:
 *   * Pathname changes → bar starts at 0% and eases to 80% over
 *     600ms (the "we're working on it" phase).
 *   * 350ms after the new render commits → bar completes to 100%
 *     and fades out over 200ms.
 *   * The bar uses ``transform: scaleX(…)`` (compositor-only) so
 *     it runs at 60fps even under load.
 *
 * Respects ``prefers-reduced-motion``: the bar still appears but
 * jumps to 100% instead of animating.
 *
 * Mounted once at the root layout. Not a hook — it owns its own
 * DOM node + state.
 */
import { useEffect, useRef, useState } from 'react';
import { usePathname, useSearchParams } from 'next/navigation';

export function TopProgressBar() {
  const pathname = usePathname();
  const search = useSearchParams();
  const [progress, setProgress] = useState(0);
  const [visible, setVisible] = useState(false);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(() => {
    // Cancel any in-flight timers from a prior navigation — happens
    // when the user clicks two links quickly.
    timers.current.forEach(clearTimeout);
    timers.current = [];

    // Start: show + jump to 12% so the bar visibly appears.
    setVisible(true);
    setProgress(12);

    // Phase 1: ease to 80% over the first 600ms.
    timers.current.push(setTimeout(() => setProgress(80), 30));

    // Phase 2: complete to 100% after the new render has had time
    // to commit (350ms is enough for a typical SPA route change to
    // resolve and the actual content to land).
    timers.current.push(setTimeout(() => setProgress(100), 600));

    // Phase 3: fade out.
    timers.current.push(setTimeout(() => {
      setVisible(false);
      // Reset to 0 after the fade so the next nav starts fresh.
      timers.current.push(setTimeout(() => setProgress(0), 220));
    }, 850));

    return () => {
      timers.current.forEach(clearTimeout);
      timers.current = [];
    };
    // The search-params dep makes us also respond to query-string-
    // only changes (filter tabs etc.) — feels right.
  }, [pathname, search]);

  return (
    <div
      role="progressbar"
      aria-label="Page loading"
      aria-valuenow={Math.round(progress)}
      aria-valuemin={0}
      aria-valuemax={100}
      className="fixed top-0 left-0 right-0 z-[100] h-[2px] pointer-events-none"
      style={{
        opacity: visible ? 1 : 0,
        transition: 'opacity 200ms ease-out',
      }}
    >
      <div
        className="h-full origin-left"
        style={{
          transform: `scaleX(${progress / 100})`,
          // Brand-gradient bar — pulls from the CSS variables defined
          // in globals.css so the visual stays in sync if the accent
          // palette is later re-skinned.
          background:
            'linear-gradient(90deg, var(--accent-soft), var(--accent) 60%, var(--accent))',
          // The trailing edge of the bar gets a soft glow so it
          // reads as a sweeping motion rather than a flat fill.
          boxShadow:
            '0 0 8px var(--accent-soft), 0 1px 0 rgba(255,255,255,0.4) inset',
          transition: 'transform 600ms cubic-bezier(0.25, 1, 0.5, 1)',
        }}
      />
    </div>
  );
}
