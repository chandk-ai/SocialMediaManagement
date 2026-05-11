/**
 * SMMS brand mark + wordmark.
 *
 * The mark is a rounded square containing a stylised "broadcast" glyph:
 * three concentric arcs radiating from a small filled node. The
 * semantic: one source → many platforms, which is literally what the
 * product does. Visually it reads as a signal / pulse / wifi-adjacent
 * shape without being a wifi icon. Scales cleanly from 16px to 64px
 * because every stroke is in viewBox units, not pixels.
 *
 * Background: a vertical accent gradient (deeper at the bottom)
 * gives the mark a sense of weight without resorting to a drop
 * shadow. The gradient stops live in the SVG defs so the mark
 * renders correctly on any background (white, dark, accent-muted)
 * without theme switching.
 *
 * Components:
 *   <Logo />          mark only — use in tight UI (toasts, favicons)
 *   <Wordmark />      mark + "SMMS" + tagline — use in headers
 *
 * Usage:
 *   <Logo size={28} />
 *   <Wordmark variant="full" />     ↳ mark + name + tagline
 *   <Wordmark variant="compact" />  ↳ mark + name only
 *   <Wordmark variant="mark" />     ↳ just the mark
 */
import { cn } from '@/lib/utils';

export function Logo({
  size = 28,
  className,
  monochrome = false,
}: {
  size?: number;
  className?: string;
  /** Render in a single accent color — for places where the gradient
   * would clash (e.g. on top of the accent itself). */
  monochrome?: boolean;
}) {
  // Deterministic gradient id so it survives SSR + multiple instances
  // on one page without colliding.
  const gid = `smms-mark-${monochrome ? 'mono' : 'grad'}`;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label="SMMS logo"
      className={cn('shrink-0', className)}
    >
      <defs>
        {!monochrome && (
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="32" gradientUnits="userSpaceOnUse">
            <stop offset="0" stopColor="#5B8DEF" />
            <stop offset="1" stopColor="#2D52CE" />
          </linearGradient>
        )}
      </defs>

      {/* Rounded square base — anchors the mark and gives it a
          recognisable silhouette at favicon scales (16px). */}
      <rect
        x="0" y="0" width="32" height="32" rx="8"
        fill={monochrome ? 'currentColor' : `url(#${gid})`}
      />

      {/* The "broadcast" glyph — three arcs radiating from a small
          node. Angles + radii tuned so the mark reads cleanly at
          16px favicon size without ink-trap. */}
      <g stroke="#FFFFFF" strokeLinecap="round" fill="none">
        {/* Node */}
        <circle cx="11.5" cy="20" r="1.6" fill="#FFFFFF" stroke="none" />
        {/* Arc 1 — innermost */}
        <path d="M14.5 18.5 a4 4 0 0 1 0 3"  strokeWidth="1.6" />
        {/* Arc 2 — middle */}
        <path d="M17.5 16 a8 8 0 0 1 0 8"   strokeWidth="1.6" opacity="0.85" />
        {/* Arc 3 — outermost */}
        <path d="M20.5 13.5 a12 12 0 0 1 0 13" strokeWidth="1.6" opacity="0.65" />
      </g>
    </svg>
  );
}


export function Wordmark({
  variant = 'compact',
  className,
}: {
  variant?: 'full' | 'compact' | 'mark';
  className?: string;
}) {
  if (variant === 'mark') {
    return <Logo size={28} className={className} />;
  }
  return (
    <div className={cn('flex items-center gap-2.5', className)}>
      <Logo size={28} />
      <div className="leading-tight">
        <div className="text-[15px] font-semibold tracking-tight text-ink-900">
          SMMS
        </div>
        {variant === 'full' && (
          <div className="text-[9px] uppercase tracking-[0.14em] text-ink-500 font-medium">
            Social Engine
          </div>
        )}
      </div>
    </div>
  );
}
