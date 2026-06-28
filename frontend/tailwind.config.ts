import type { Config } from 'tailwindcss';
import animate from 'tailwindcss-animate';

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
        line: { DEFAULT: '#dedcd8', strong: '#c8c6c2' },
        primary: { DEFAULT: '#0066ff', tint: '#eef4ff' },
        success: { DEFAULT: '#12a150', tint: '#ddf7e9' },
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
  plugins: [animate],
} satisfies Config;
