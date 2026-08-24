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
    // One rhythm for every gap, padding and margin in the app, replacing Tailwind's
    // numeric scale outright — a gap and a padding are the same measurement seen from
    // two sides, and keeping them in separate scales is how `gap-md` (10px) ended up
    // beside `px-3` (12px) in one row. The numbers are the design's own, not a 4px
    // grid: the app had two rhythms, its designer's and Tailwind's, and the design
    // source is the one that wins.
    //
    // `hair` and `xs` separate stacked hairlines (a counter over its label); `tight`
    // separates parts of one thing (a dot from its label); `sm` is the default inside a
    // control or a row; `md` separates rows and fields inside a card; `lg` the blocks of
    // a card or form; `xl` and `2xl` are a control's own horizontal padding. `0` and
    // `px` stay because they are not steps — `px` is the hairline a grid uses for its
    // own dividers.
    //
    // `page` and `empty` are the two rungs that belong to the page rather than to
    // anything on it: the room a page's own content stands in, and the height an empty
    // state fills. They carry names instead of magnitudes because a component reaching
    // for the widest number it can find is exactly how they got spent, and `p-page` on a
    // card reads as the mistake it is where `p-4xl` read as a size. The 26px rung that
    // used to sit under them is gone into `page`: its eight sites were a page's own
    // padding written 6px short, and with 72% of this scale's 1384 uses on `sm`, `md`
    // and `lg`, the middle is where the rhythm has to be exact and the top is where it
    // has to be few.
    //
    // Replacing rather than extending — which is what makes `p-4` an error instead of a
    // habit — only became safe once a component's dimensions moved out: `spacing` also
    // feeds seventeen core scales including `w-*` and `h-*`, and a 34px avatar is not a
    // rung of a rhythm. The dimension scales below take that job, so `p-md` still paints
    // 10px and `w-md` now paints nothing.
    spacing: {
      0: '0px',
      px: '1px',
      hair: '2px',
      xs: '3px',
      tight: '5px',
      sm: '7px',
      md: '10px',
      lg: '14px',
      xl: '18px',
      '2xl': '22px',
      page: '32px',
      empty: '64px',
    },
    // A component's own dimensions, and the reason the rhythm above could be replaced.
    // In Tailwind a `theme.spacing` key becomes `p-<key>` AND `w-<key>`; declaring
    // `width`, `height`, `minWidth`, `maxWidth`, `minHeight`, `maxHeight` and `size` as
    // their own top-level keys is the only way to stop one table answering two
    // questions. While it answered both, 476 dimension sites spent 73 distinct values,
    // 57 of which no rung of the rhythm named — the exemption for them in the lint rule
    // is what let the drift run.
    //
    // Every name here is a ROLE, and each one had to name a thing this product has, with
    // a component that can be pointed at wearing it. `coin` for 52px fails that test;
    // `touch` for the 44px tap target passes it. A magnitude word cannot argue the next
    // value down onto an existing rung, and arguing values down is the whole job.
    //
    // A role also has to be worn twice. A rung with one wearer is a literal with a name,
    // and worse than the literal: it teaches the next reader that any one-off deserves a
    // rung, which is how a closed set reopens. Nine came back out — AddStoryModal's
    // layout tile and story preview, NeuroAccountsModal's spend gauge, AccountsPage's
    // search field, WarmingBoard's embedded log, LoginPage's card, SettingsPage's column,
    // ProxyPool's empty-state sentence, ScenarioCard's prompt column — each one
    // component's internal layout, so each stays at its call site as an arbitrary value,
    // named in the lint rule's exemption instead of here. `0`, `px`, `auto`, `max`,
    // `full` and `screen` are outside that count for the same reason they are outside the
    // rhythm's: they are shapes and keywords, not steps.

    // Squares, where width and height are one decision — 131 elements used to say the
    // same number twice. Eleven rungs absorb twenty-nine circle diameters and every
    // icon-sized box in the app, and no fold moved a value more than 4px.
    //
    // `tick` is the smallest mark drawn (WarmDaysModal's slider ticks, DialogueFeed's
    // typing dot); `dot` the status dot beside a label (Badge, AccountsTable's proxy
    // dot, AppNav's system dot); `node` a marker on a stepper's rail (WarmingBoard,
    // PipelineCard); `spinner` the `tb-spin` ring and the check a finished step wears;
    // `glyph` a round badge carrying a character (HelpHint's `?`, _CheckRow's checkbox,
    // HowItWorksCard's numeral, WarmDaysModal's thumb); `chip` the smallest thing that
    // can be pressed (IconButton `sm`, PhotoTab's remove-over-image); `icon` the
    // standalone icon button (IconButton `md`, AppNav's logo mark); `tile` the icon
    // beside a modal title and an account's face in a table row (IconButton `lg`);
    // `thumbnail` a post's picture (ChannelPostsPanel); `touch` the tap target the
    // mobile nav is built from (IconButton `touch`, NavDrawer's rows); `face` an
    // account's own portrait at the top of ProfileModal and AccountEdit.
    size: {
      tick: '4px',
      dot: '7px',
      node: '9px',
      spinner: '14px',
      glyph: '18px',
      chip: '22px',
      icon: '28px',
      tile: '34px',
      thumbnail: '38px',
      touch: '44px',
      face: '52px',
    },
    // Heights that are not a square's side: something either lies flat (a rail, a bar)
    // or stands to a control's own height. `rail` is a hairline that fills (AppNav's
    // active underline, both steppers' connectors, AddAccountModal's step bar); `meter`
    // the progress bar a value fills (AccountEdit's warm-up, ProxyPool's capacity,
    // WarmDaysModal's slider rail); `flag` the country flag, which the app drew at six
    // different sizes for one job, and nothing else — NeuroAccountsModal's spend gauge
    // rode this rung because it measures 13px too, and a gauge is not a flag; `badge` a
    // count that grows sideways rather than taller, so it cannot be a square
    // (ListenerCard's counter, AddStoryModal's photo index) and pairs with the `minWidth`
    // rung of the same name; `bar` a column of WarmingBoard's day histogram, and the
    // compact buttons that stand as tall; `compact` a control shorter than a field
    // (Switch's track, WarmDaysModal's slider, ScenarioCard's delete); `header` the app
    // bar (AppNav, NavDrawer).
    height: {
      px: '1px',
      full: '100%',
      rail: '2px',
      meter: '6px',
      flag: '13px',
      badge: '18px',
      bar: '22px',
      compact: '28px',
      header: '56px',
    },
    // Widths, which unlike heights are mostly bands rather than sizes: a column, a
    // truncation, a dialog. `flag` is the country flag's own width; `action` the fixed
    // column a ROW gives to a control, and nothing else — CampaignsCard's and
    // ListenerCard's play/stop/edit/delete buttons, ApiKeyField's reveal,
    // DiscoveryResults' checkbox column. Switch's track, AccountsTable's trust bar and
    // AddStoryModal's layout tile also measured 46px and were wearing this rung for it,
    // which is measuring the same rather than being the same: none of the three sits in a
    // row's column, and each is now its own component's business. `number` is a number
    // input (WarmConfigModal's day box, CommentModeFields' time box); `readout` a figure
    // that must not reflow as it changes (CampaignSetupCard's slider readout,
    // AccountLimitsModal's limit), which AddStoryModal's story card happens to share;
    // `stamp` a table column holding a time or an id (LogsPage, LogTerminal's channel);
    // `col` the ordinary table column and the inline field in a row; `menu` a dropdown
    // or a filter beside a page title (AppNav's account menu); `tip` HelpHint's tooltip
    // and AccountsPage's search.
    //
    // The last four are the dialogs, and they are the reason this scale exists. Twenty-two
    // modals were spending eleven widths: 380, 420, 440, 460, 468, 480, 540, 560, 580,
    // 760 and 920, which is not a scale but a record of what each one happened to be born
    // at. `confirm` is a question with two buttons, `form` a dialog you fill in, `panel`
    // one that holds a list or a tabbed body, `table` one built around a table.
    //
    // `table` is the one rung here whose value a component dictates rather than a
    // designer: DataTable renders cards instead of a table below 880px of MEASURED width,
    // so a dialog built around a table has to clear 880 plus its own chrome or it never
    // shows a table at all. CommentHistoryModal's `px-2xl` body and the hairline border of
    // the card it wraps the table in cost 46 of those, which is where 926 comes from;
    // ChannelDiscoveryModal's `p-xl` costs 36 and clears it by ten. 920 — the widest thing
    // the app was born with, and the number this rung took first — leaves the history
    // dialog 874 and renders it as cards, which is the rung naming something neither
    // wearer draws.
    width: {
      px: '1px',
      auto: 'auto',
      max: 'max-content',
      full: '100%',
      flag: '18px',
      action: '46px',
      number: '64px',
      readout: '74px',
      stamp: '120px',
      col: '150px',
      menu: '190px',
      tip: '230px',
      confirm: '420px',
      form: '480px',
      panel: '560px',
      table: '926px',
    },
    // Floors. `0` is the one that lets a flex child truncate instead of pushing its row
    // wide, which is why it outnumbers every other dimension token in the app. `badge`
    // is a count that must stay round at one digit (ListenerCard's counter,
    // AddStoryModal's photo index); `col` the width a table column refuses to go under
    // (AccountEdit's header columns, AccountsPage's filter); `table` the point below which
    // DataTable stops being a table and becomes cards, and therefore the floor every
    // `w-table` dialog is sized around. `table` is the one rung here a single component
    // wears, and it keeps its name because two other places already say the same number:
    // useWideViewport's `TABLE_MIN_WIDTH`, which is this measurement said in JS, and
    // `width.table` above, which is this plus a dialog's own padding.
    minWidth: {
      0: '0px',
      badge: '18px',
      col: '150px',
      table: '880px',
    },
    // Ceilings, all of them about reading rather than fitting. `name` is the cap on a
    // channel or comment that has to truncate (DiscoveryResults, NeurocommentBoard);
    // `page` a page's content column (AccountEdit, NeuroshillingPage); `shell` the app's
    // own width (AppShell, AppNav).
    maxWidth: {
      full: '100%',
      name: '240px',
      page: '1000px',
      shell: '1340px',
    },
    // `touch` is the same 44px as the square rung, said about one axis: NavDrawer's rows
    // are as tall as a tap target but as wide as the drawer.
    minHeight: {
      touch: '44px',
      screen: '100vh',
    },
    // Scroll caps. `feed` is a scrolling list that owns its block (LogTerminal,
    // NeurocommentBoard's comments, CampaignPromptModal's accounts, DialogueFeed);
    // `dialog` the cap on a dialog's body, in `dvh` because the browser chrome on a phone
    // is part of what it has to clear.
    maxHeight: {
      feed: '220px',
      dialog: '88dvh',
    },
    extend: {
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
