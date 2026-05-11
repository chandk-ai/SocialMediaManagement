import type { Config } from 'tailwindcss';

/**
 * Tailwind config — minimal, low-noise design system.
 *
 * Palette philosophy: one neutral scale (``ink``) does 80% of the
 * work; one accent (``accent``) carries every interactive cue. Both
 * have soft / strong variants for hover states and selected pills
 * without needing arbitrary opacity values inline.
 *
 * Typography: Inter as the everywhere face (excellent legibility at
 * small sizes, broad weight range). JetBrains Mono for IDs, code,
 * status pills — its zero is slashed which prevents O/0 confusion in
 * job IDs and tokens.
 *
 * Shadows: three tiers — ``card-sm`` for resting cards, ``card`` for
 * hovered ones (lifts the surface 1–2px feel), ``card-lg`` for
 * floating drawers + popovers. Each is tinted with the ink-900 hue
 * so shadows pick up the page's color temperature rather than being
 * neutral grey.
 */
const config: Config = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // ink — the neutral spine. Cool-leaning grey-blue so it pairs
        // naturally with the accent without competing.
        ink: {
          50:  '#f8f9fb',
          100: '#eef0f4',
          200: '#dde1e9',
          300: '#bcc3cf',
          400: '#8f99ac',
          500: '#5a6479',
          600: '#3f485c',
          700: '#2c3344',
          800: '#1a2030',
          900: '#0e1422',
        },
        // accent — the only fully-saturated colour in the system.
        // ``DEFAULT`` for primary buttons / brand mark; ``soft`` for
        // hover states + selected pills; ``strong`` for pressed /
        // active. ``muted`` is the lightest tint, for full-section
        // tinted backgrounds.
        //
        // Wellness-green emerald palette: positive-action energy
        // (#22c55e is the "go" green) without leaving the saturated,
        // confident range that B2B tools live in. Pairs cleanly with
        // the cool-leaning ink scale — same combo Notion + Linear use.
        accent: {
          DEFAULT: '#16a34a',   // emerald-600 — primary
          fg:      '#ffffff',
          soft:    '#22c55e',   // emerald-500 — hover ramp + gradient top
          strong:  '#15803d',   // emerald-700 — pressed / gradient bottom
          muted:   '#ecfdf5',   // emerald-50  — tinted surfaces
        },
        // brand — the surface gradient used by the Logo SVG so the
        // values stay in sync with the rest of the system.
        brand: {
          from: '#22C55E',
          to:   '#15803D',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
        display: ['Inter', 'system-ui', 'sans-serif'],
      },
      fontSize: {
        // Tighter display sizes for hero headers; the default text
        // sizes stay Tailwind-standard so nothing existing reflows.
        'display-xl': ['3rem', { lineHeight: '1.05', letterSpacing: '-0.025em' }],
        'display-lg': ['2.25rem', { lineHeight: '1.1', letterSpacing: '-0.02em' }],
        'display':    ['1.5rem', { lineHeight: '1.2', letterSpacing: '-0.015em' }],
      },
      letterSpacing: {
        tightish: '-0.012em',
      },
      boxShadow: {
        // Cool-tinted shadows — pick up the ink-900 hue so the depth
        // reads as part of the same colour system, not a foreign grey.
        'card-sm': '0 1px 2px rgba(15, 20, 40, 0.04)',
        'card':    '0 1px 2px rgba(15, 20, 40, 0.05), 0 4px 12px rgba(15, 20, 40, 0.04)',
        'card-lg': '0 4px 8px rgba(15, 20, 40, 0.06), 0 24px 48px rgba(15, 20, 40, 0.08)',
        'inner-sm': 'inset 0 1px 0 rgba(255, 255, 255, 0.06)',
        'ring-accent': '0 0 0 3px rgba(22, 163, 74, 0.20)',
      },
      borderRadius: {
        lg:   '0.625rem',
        xl:   '0.75rem',
        '2xl':'1rem',
      },
      backgroundImage: {
        // Subtle page-background grain so the white cards have
        // something to push off — keeps the canvas from feeling
        // flat without becoming busy.
        // Soft emerald wash on the page canvas — gentle enough to read
        // as ambient light rather than a coloured background. Two
        // off-axis radial stops avoid the "tinted glass" look.
        'page-grad': 'radial-gradient(1200px 800px at 100% 0%, rgba(22,163,74,0.045), transparent 60%), radial-gradient(900px 700px at 0% 100%, rgba(34,197,94,0.035), transparent 50%)',
        'accent-grad': 'linear-gradient(135deg, #22C55E 0%, #15803D 100%)',
      },
      transitionTimingFunction: {
        'out-quart': 'cubic-bezier(0.25, 1, 0.5, 1)',
      },
    },
  },
  plugins: [],
};
export default config;
