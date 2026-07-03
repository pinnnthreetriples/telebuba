import type { Config } from 'tailwindcss';

// Design tokens — the single source of truth for the SPA's palette/typography,
// extracted from the design file in web/ (the dc design system) before web/ is
// removed (#173). Values are the dominant colours of that design.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        canvas: '#f1efed',
        surface: '#faf9f7',
        ink: { DEFAULT: '#0b0b0c', muted: '#74726e', subtle: '#9a9893' },
        line: { DEFAULT: '#e6e5e3', strong: '#d8d6d2', input: '#dedcd8' },
        track: '#eeedea',
        primary: { DEFAULT: '#0066ff', tint: '#eef4ff' },
        success: { DEFAULT: '#12a150', tint: '#ddf7e9', dot: '#16b364' },
        danger: { DEFAULT: '#c0473f', tint: '#fbecec' },
        warning: { DEFAULT: '#9a7b22' },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      borderRadius: { lg: '12px', md: '8px', sm: '6px' },
    },
  },
  plugins: [],
} satisfies Config;
