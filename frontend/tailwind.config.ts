import type { Config } from 'tailwindcss';

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
        // Minimal, low-noise palette
        ink: {
          50:  '#f8f9fb',
          100: '#eef0f4',
          200: '#dde1e9',
          300: '#bcc3cf',
          500: '#5a6479',
          700: '#2c3344',
          900: '#0e1422',
        },
        accent: {
          DEFAULT: '#3a6df0',
          fg: '#ffffff',
          muted: '#eaf0ff',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
      },
      boxShadow: {
        card: '0 1px 2px rgba(15,20,40,.04), 0 1px 1px rgba(15,20,40,.03)',
      },
      borderRadius: {
        xl: '0.75rem',
        '2xl': '1rem',
      },
    },
  },
  plugins: [],
};
export default config;
