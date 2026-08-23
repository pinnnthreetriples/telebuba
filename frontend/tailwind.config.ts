import type { Config } from 'tailwindcss';

// Design tokens — the single source of truth for the SPA's palette/typography.
// Every colour the UI paints has a name here; a raw `#hex` in a component is a
// bug, not a shortcut (the design-system canon lists the merges behind this set).
//
// Each semantic colour carries the same three roles, so a component never has to
// invent a shade: DEFAULT for text and icons, `tint` for the surface behind them,
// `line` for that surface's border. Blue adds `wash` (the lightest hover fill)
// and `press` (filled-button hover). `term` is the one dark surface — the log
// terminal and the tooltips that share its ink; its text/link/error shades are
// lighter than the light-theme ones on purpose, because #0066ff and #c0473f do
// not read on #16161a.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    // Four corner radii and the pill, replacing Tailwind's scale outright so the
    // names that are left are the only ones a component can reach for: `sm` for
    // controls that sit inside another box (checkbox, tiny icon button), `md` for
    // standalone controls (button, input, tap target), `lg` for panels nested in a
    // card (row, dropdown, tooltip), `card` for the card itself. `none`/`full`
    // stay because they are shapes, not steps. Hairline elements — a 2px progress
    // bar, a flag, a chat bubble's tail — keep their own `rounded-[2px]`/`[3px]`:
    // snapping those up to 6px would round them away entirely.
    borderRadius: {
      none: '0px',
      sm: '6px',
      md: '8px',
      lg: '11px',
      card: '16px',
      full: '9999px',
    },
    // Four rungs of breathing room, replacing Tailwind's 4/8/12/16 scale outright for
    // the same reason the radii are replaced: the numeric names resolved to values the
    // canon does not have, so `gap-2` and `gap-[7px]` sat side by side in one row and
    // disagreed by a pixel. Gone, they cannot come back. `tight` separates parts of one
    // thing (a dot from its label, an icon from its number), `sm` is the default inside
    // a control or a row, `md` separates rows and fields inside a card, `lg` separates
    // the blocks of a card or a form. `0` and `px` stay because they are not steps —
    // `px` is the hairline a grid uses for its own dividers. Denser rows keep their own
    // arbitrary `gap-[2px]`/`gap-[3px]` (arbitrary values still work with a replaced
    // scale): snapping a 2px stack of counters up to 5px would re-lay it out.
    gap: { 0: '0px', px: '1px', tight: '5px', sm: '7px', md: '10px', lg: '14px' },
    // Four stacking layers, lowest to highest, so a component never has to guess a
    // number: `raised` lifts content over its own siblings, `sticky` is the app
    // header that survives scrolling, `pop` is anything that floats over the page
    // (dropdown, tooltip, menu) and therefore must clear the sticky header, and
    // `dialog` is the modal layer — above everything, including its own backdrop's
    // neighbours. `0` stays as the one way to opt out (the segmented control's
    // sliding pill sits under its own labels, not over its neighbours).
    zIndex: {
      0: '0',
      raised: '1',
      sticky: '10',
      pop: '20',
      dialog: '30',
    },
    extend: {
      colors: {
        canvas: '#f1efed',
        surface: '#faf9f7',
        track: '#eeedea',
        ink: { DEFAULT: '#0b0b0c', body: '#3a3a3a', muted: '#74726e', subtle: '#9a9893' },
        line: { DEFAULT: '#e6e5e3', strong: '#d8d6d2', input: '#dedcd8', row: '#f0eeeb' },
        primary: {
          DEFAULT: '#0066ff',
          press: '#0057db',
          tint: '#eef4ff',
          wash: '#f2f6ff',
          line: '#cbd7ec',
        },
        success: { DEFAULT: '#12a150', tint: '#ddf7e9', line: '#b8ecce', dot: '#16b364' },
        // `deep` is the one amber the set lacked: the darkest rung, for a heading or a
        // small icon sitting ON an amber chip, where DEFAULT is the subtitle beside it.
        // Two notices needed it — neurocomment's IdleBanner (heading over subtitle) and
        // the proxy pool's geo-conflict chip — plus ProfileModal's "bio not applied"
        // line, where DEFAULT only measures 4.0:1 on white and this measures 6.1:1.
        warning: {
          DEFAULT: '#9a7b22',
          deep: '#7a5e12',
          strong: '#c47d12',
          tint: '#fff0d2',
          line: '#efd79a',
        },
        danger: { DEFAULT: '#c0473f', tint: '#fbecec', line: '#f0c9c5' },
        term: {
          DEFAULT: '#16161a',
          dim: '#5c5c66',
          text: '#c9c9d3',
          link: '#6ea8fe',
          error: '#e5736b',
          success: '#7be0a6',
          warning: '#ffd27f',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      // Three elevations, one per purpose: `pop` for anything that floats over the
      // page (dropdown, tooltip, toast, menu), `ring` for the hairline outline that
      // stands in for a border on a tinted pill, `thumb` for the switch knob.
      boxShadow: {
        pop: '0 10px 30px rgba(11,11,12,0.12)',
        ring: '0 0 0 1px rgba(11,11,12,0.07)',
        thumb: '0 1px 3px rgba(0,0,0,0.3)',
      },
    },
  },
  plugins: [],
} satisfies Config;
