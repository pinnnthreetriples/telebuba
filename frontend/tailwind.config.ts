import type { Config } from 'tailwindcss';

// Design tokens — the single source of truth for the SPA's palette/typography.
// Every colour the UI paints has a name here; a raw `#hex` in a component is a
// bug, not a shortcut (the design-system canon lists the merges behind this set).
//
// Each semantic colour carries the same three roles, so a component never has to
// invent a shade: DEFAULT for text and icons, `tint` for the surface behind them,
// `line` for that surface's border. Blue adds `press` (filled-button hover), and
// its `tint` doubles as the hover fill. `term` is the one dark surface — the log
// terminal and the tooltips that share its ink; its text/link/error shades are
// lighter than the light-theme ones on purpose, because #0066ff and #c0473f do
// not read on #16161a.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    // Five elevations, one per purpose, replacing Tailwind's `sm…2xl` scale outright —
    // the last scale still sitting in `extend`, which is how `shadow-2xl` stayed
    // reachable next to a canon that names five. `pop` is anything that floats over the
    // page (dropdown, tooltip, toast, menu — ALL of them, which is the point: those four
    // things had four different shadows); `ring` is the hairline outline that stands in
    // for a border on a tinted pill; `thumb` is a knob you can drag (the switch, the
    // warming-days slider); `focus` is the ring a focused control wears, the one recipe
    // shared by `shared/ui/Select`'s trigger and index.css's `.tb-time:focus-within`,
    // which is what makes it a role rather than a one-off; `seg` is the raised active
    // segment of an inset segmented tray — Tailwind's own `shadow-sm` value kept
    // verbatim, so naming it costs nothing and changes nothing. `none` stays because it
    // is the absence of a shadow, not a step.
    boxShadow: {
      none: 'none',
      pop: '0 10px 30px rgba(11,11,12,0.12)',
      ring: '0 0 0 1px rgba(11,11,12,0.07)',
      thumb: '0 1px 3px rgba(0,0,0,0.3)',
      focus: '0 0 0 3px rgba(0,102,255,0.12)',
      seg: '0 1px 2px 0 rgb(0 0 0 / 0.05)',
      // The sliding pill of a segmented tab strip: a filled blue lozenge that has to
      // read as sitting ON the tray rather than in it, which its own colour cannot do.
      pill: '0 1px 2px rgba(0,102,255,0.3)',
    },
    // The eight type sizes the UI actually has, replacing Tailwind's `xs…9xl` scale
    // outright — nothing used a single one of those names, and leaving them reachable
    // would mean `text-sm` (14px) could arrive tomorrow next to a `text-lead` (13px)
    // heading and disagree by a pixel, the way `gap-2` and `gap-[7px]` used to.
    // `micro` is metadata under a row (a timestamp, a counter), `tiny` a caption or a
    // pill's label, `body` the default for everything inside a card, `lead` a form
    // control's own text and the one step up for a card's primary line, `title` a
    // card or modal heading, `stat` an odometer's number, `display` a page heading,
    // `hero` the one empty-state numeral. Bare strings, deliberately: `leading-*`
    // stays an independent decision, and pairing a line-height into these rungs would
    // silently re-space every one of the 694 sites that just moved onto them.
    fontSize: {
      micro: '10.5px',
      tiny: '11px',
      body: '12.5px',
      lead: '13px',
      title: '16px',
      stat: '20px',
      display: '22px',
      hero: '42px',
    },
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
    // Four motion rungs, replacing Tailwind's `duration-75…1000` scale outright for the
    // same reason the type scale went: nothing used a single one of those names, and the
    // numbers were what people copied. `state` is a hover or a colour change — something
    // already on screen reacting; `enter` is something arriving (a toast, an opacity
    // fade-in); `reveal` is a panel opening and every part of that one gesture, chevron
    // included; `roll` is the odometer counting. A gesture that spans two elements must
    // spend ONE rung on both halves — the dropdown's panel and its chevron used to run
    // 420ms against 400ms, which is a 20ms stagger nobody chose.
    transitionDuration: {
      state: '150ms',
      enter: '250ms',
      reveal: '420ms',
      roll: '900ms',
    },
    // Two curves, replacing Tailwind's `ease-linear/in/out/in-out` outright — none of
    // which was used, and `out` deliberately takes the name `ease-out` so that the one
    // "settle" curve in the app is the one a component reaches for. `out` decelerates
    // hard and stops (a rail filling, a number rolling); `spring` overshoots slightly
    // and settles back (a panel opening, a chevron flipping). The CSS side carried a
    // third curve, `cubic-bezier(.34,1.4,.6,1)`, 0.05 of overshoot away from `spring`
    // and used on the very panel whose chevron used `spring` — that is drift, not a
    // third intention, so it is gone.
    transitionTimingFunction: {
      out: 'cubic-bezier(.16,1,.3,1)',
      spring: 'cubic-bezier(.34,1.45,.6,1)',
    },
    // Five stacking layers, lowest to highest, so a component never has to guess a
    // number: `raised` lifts content over its own siblings, `sticky` is the app
    // header that survives scrolling, `pop` is anything that floats over the page
    // (dropdown, tooltip, menu) and therefore must clear the sticky header,
    // `dialog` is the modal layer — above everything, including its own backdrop's
    // neighbours — and `toast` is the one thing above THAT. `0` stays as the one way
    // to opt out (the segmented control's sliding pill sits under its own labels,
    // not over its neighbours).
    //
    // `toast` is its own rung rather than sharing `dialog`: a toast reports the
    // outcome of an action, and the dialog that action was taken in is usually still
    // open, so it has to clear it. Sharing the rung made that a question of document
    // order, which holds only while every toast is raised AFTER its dialog opened —
    // a toast already on screen when a dialog opens loses the tie and is painted
    // over. Order-independent is the whole point of naming a layer.
    zIndex: {
      0: '0',
      raised: '1',
      sticky: '10',
      pop: '20',
      dialog: '30',
      toast: '40',
    },
    extend: {
      // One rhythm for every gap, padding and margin in the app, replacing the four-rung
      // `gap` scale that used to live here on its own — a gap and a padding are the same
      // measurement seen from two sides, and keeping them in separate scales is how
      // `gap-md` (10px) ended up beside `px-3` (12px) in one row. The numbers are the
      // design's own, not a 4px grid: the app had two rhythms, its designer's and
      // Tailwind's, and the design source is the one that wins.
      //
      // `hair` and `xs` separate stacked hairlines (a counter over its label); `tight`
      // separates parts of one thing (a dot from its label); `sm` is the default inside a
      // control or a row; `md` separates rows and fields inside a card; `lg` the blocks of
      // a card or form; `xl` and `2xl` are a control's own horizontal padding; `3xl` and
      // up are a page's own breathing room and an empty state's. `0` and `px` stay because
      // they are not steps — `px` is the hairline a grid uses for its own dividers.
      //
      // Added rather than replacing Tailwind's numeric scale outright, because `spacing`
      // also feeds `w-*`/`h-*`, and a 34px avatar or a 6px progress bar is a component's
      // dimension, not a rung of this rhythm. What keeps `p-4` from coming back beside
      // `px-xl` is the lint rule that forbids it (eslint.config.js), not the absence of
      // the key.
      spacing: {
        hair: '2px',
        xs: '3px',
        tight: '5px',
        sm: '7px',
        md: '10px',
        lg: '14px',
        xl: '18px',
        '2xl': '22px',
        '3xl': '26px',
        '4xl': '32px',
        '5xl': '64px',
      },
      colors: {
        // Two jobs, one colour: the ground the page sits on, and the fill of anything
        // that is filled ON a card — a progress rail, a chip, a counter. They were two
        // tokens three units apart, which is under the threshold at which a flat area
        // reads as a different colour at all, and no filled thing in the app ever lies
        // straight on the page for the difference to have to carry.
        canvas: '#f1efed',
        // The wash over an image that a control has to stay legible on — a photo tile's
        // remove button. Dark ink at 55%, not a flat grey: what is behind it is a
        // photograph, and a solid fill would read as a hole punched in it.
        scrim: 'rgba(11,11,12,0.55)',
        surface: '#faf9f7',
        // `muted` and `subtle` are the two greys small text is written in, so both sit
        // at the AA floor rather than where they looked best: `muted` cleared only
        // 4.10:1 on the neutral fill (fourteen pills pair them) and `subtle` 2.88:1 on white
        // across seventeen timestamps, placeholders and empty states. AA leaves little
        // room between them and `body`, so the ramp is compressed on purpose — the
        // alternative is a rung the design system knows cannot be read.
        ink: { DEFAULT: '#0b0b0c', body: '#3a3a3a', muted: '#63615d', subtle: '#6e6b66' },
        // A field's border is DEFAULT too. It had a darker rung of its own, eight units
        // down, on the theory that a box you type into has to announce itself — but a
        // field is read as a field by its shape and its white fill against the card
        // around it, and every field in the app does sit on white.
        line: { DEFAULT: '#e6e5e3', strong: '#d8d6d2', row: '#f0eeeb' },
        primary: {
          DEFAULT: '#0066ff',
          press: '#0057db',
          // Also the hover fill. `wash` was a second pale blue four units lighter, which
          // is a difference a flat area cannot show; the one place the two ever met is
          // WarmingBoard's pipeline panel inside its tinted card, where four units never
          // bought a visible edge in the first place.
          tint: '#eef4ff',
          line: '#cbd7ec',
          // Not `line` a notch lighter by accident: this is a border faint enough to
          // double as a divider FILL, which is the whole reason it has to exist. The
          // neurocomment PipelineCard needs both jobs from one colour — its card border,
          // and the background of a `gap-px` grid whose 1px gaps ARE the tile dividers.
          // `line` is far too dark for that fill and `tint` far too blue for the border.
          hairline: '#e4ecfa',
          // The blue that small text on `tint` is written in. DEFAULT measures
          // 4.38:1 there — under the 4.5:1 floor by a margin nobody can see and every
          // contrast checker reports. Same role `success.deep` and `warning.deep` play.
          deep: '#0052cc',
        },
        // `deep` and `press` mirror the amber and blue rungs: `deep` is the darkest green,
        // for the heading of a notice on a green surface (WarmingBoard's "прогрет" block,
        // where DEFAULT measures only 2.97:1 on `tint` and this measures 5.85:1), and
        // `press` is the filled green button's hover, exactly as `primary.press` is the
        // blue one's — it also lifts the white label on it from 3.37:1 to 4.32:1.
        success: {
          DEFAULT: '#12a150',
          deep: '#0b6b37',
          press: '#0e8c45',
          tint: '#ddf7e9',
          line: '#b8ecce',
        },
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
        // `deep` is the red small text on a red chip is written in: DEFAULT measures
        // 4.34:1 on `tint`, and every «удалён» chip in the app is 10.5px.
        danger: { DEFAULT: '#c0473f', deep: '#a83a33', tint: '#fbecec', line: '#f0c9c5' },
        term: {
          DEFAULT: '#16161a',
          dim: '#80808c',
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
    },
  },
  plugins: [],
} satisfies Config;
