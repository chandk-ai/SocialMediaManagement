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
        accent: {
          DEFAULT: '#3a6df0',
          fg:      '#ffffff',
          soft:    '#5b8def',
          strong:  '#2d52ce',
          muted:   '#eaf0ff',
        },
        // brand — the surface gradient used by the Logo SVG so the
        // values stay in sync with the rest of the system.
        brand: {
          from: '#5B8DEF',
          to:   '#2D52CE',
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
        'ring-accent': '0 0 0 3px rgba(58, 109, 240, 0.18)',
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
        'page-grad': 'radial-gradient(1200px 800px at 100% 0%, rgba(58,109,240,0.05), transparent 60%), radial-gradient(900px 700px at 0% 100%, rgba(91,141,239,0.04), transparent 50%)',
        'accent-grad': 'linear-gradient(135deg, #5B8DEF 0%, #2D52CE 100%)',
      },
      transitionTimingFunction: {
        'out-quart': 'cubic-bezier(0.25, 1, 0.5, 1)',
      },
    },
  },
  plugins: [],
};
export default config;
