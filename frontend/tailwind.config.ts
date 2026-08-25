import type { Config } from 'tailwindcss';
import plugin from 'tailwindcss/plugin';

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
    // Six elevations, one per purpose, replacing Tailwind's `sm…2xl` scale outright —
    // this was the last scale sitting in `extend`, which is how `shadow-2xl` stayed
    // reachable next to a canon that named six. `pop` is anything that floats over the
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
    // The roles the type scale is spent on, and the layer above `shared/ui` names one
    // of these instead of respelling a rung, a weight and a grey at every site. The
    // rungs above answer "how big"; a role answers "what is this text to the reader",
    // which is the question a page can actually get right.
    //
    // The scale it replaces was not eight rungs, it was ninety-six spellings: 528
    // places wrote a rung beside a weight and an ink, and the same job came out three
    // ways at a time. A caption was `ink-subtle` 57 times, `ink-muted` 13 and colourless
    // 9; a card's heading was `semibold` 30 times, `bold` 5 and `medium` 4. Neither
    // spread was a decision — nobody can see 600 against 700 at 13px, and the grey a
    // caption ended up in was the grey the file next door happened to use.
    //
    // Each role had to be nameable in one sentence without the word "or", and had to be
    // worn by two components in different slices. That is what kept the set at twelve:
    // a status pill's label, a hand-written button's label and an avatar's initials all
    // looked like candidates and are none — they are a control's own face, which is what
    // `shared/ui` exists to own, and giving them a role here would make that layering
    // debt permanent by naming it.
    //
    // The colour is the token's own name, resolved by the plugin below, so a role cannot
    // drift from the palette. Line height is deliberately NOT part of a role, for the
    // same reason it is not part of a rung: `leading-*` stays its own decision.
    typeRole: {
      // The heading that names the screen the operator is on. Six `<h1>`s, one per page,
      // and the only role whose wearers cannot span two slices — a page title lives in
      // `pages/` by definition, so the second-wearer test is six independent features.
      'page-title': { size: 'display', weight: '700', ink: 'ink', tracking: '-0.02em' },
      // The heading a dialog opens with (ConfirmModal, every delete sheet, ProfileModal,
      // ChannelDiscoveryModal). Four slices; the widest-worn role in the set.
      'dialog-title': { size: 'title', weight: '700', ink: 'ink' },
      // The sentence a dialog asks before its buttons. Its own role rather than `prose`
      // because every confirm sheet in the app — and ConfirmModal, which is the one in
      // `shared/ui` they were all copied from — asks it one rung up and one grey darker
      // than a page explains itself in, and that is a decision, not a drift.
      'dialog-body': { size: 'lead', weight: '400', ink: 'ink-muted' },
      // The heading of the block it stands in — CollapsibleCard's `header` slot, the name
      // on a card that is one account, the name of a setting over its own description.
      'card-title': { size: 'lead', weight: '600', ink: 'ink' },
      // The name of one item inside a card: a row's subject, a group of fields' subject.
      'item-title': { size: 'body', weight: '600', ink: 'ink' },
      // The label that opens a group of settings, set in caps. The one role that carries
      // letter-spacing, which is why it exists rather than being `caption` in bold: its
      // four wearers agreed on 0.04em and a fifth had drifted to 0.03em.
      eyebrow: {
        size: 'tiny',
        weight: '600',
        ink: 'ink-subtle',
        tracking: '0.04em',
        caps: 'uppercase',
      },
      // The name of the control beside it — a field's label, a setting's name in a row.
      label: { size: 'body', weight: '500', ink: 'ink-body' },
      // The datum a row reads out: a table cell, the right-hand half of a key/value line.
      // Same rung as `prose` and a step darker, because it is the thing the operator
      // came to read and prose is the explanation around it.
      value: { size: 'body', weight: '400', ink: 'ink-body' },
      // A sentence the operator reads: an explanation, an empty state, a dialog's question.
      prose: { size: 'body', weight: '400', ink: 'ink-subtle' },
      // The small line that qualifies the control above it — a hint, a unit, a field error
      // once it takes `text-danger`. The most-worn role in the app.
      caption: { size: 'tiny', weight: '400', ink: 'ink-subtle' },
      // The smallest line: what dates or counts the row beside it.
      meta: { size: 'micro', weight: '400', ink: 'ink-subtle' },
      // The number a counter puts on the screen. Colourless in practice — every wearer
      // passes the tone the number MEANS — but it resolves to `ink` so the class is
      // complete on its own.
      stat: { size: 'stat', weight: '700', ink: 'ink' },
    },
    // Two line-heights and the glyph, replacing Tailwind's `leading-3…10/tight/snug/
    // normal/relaxed/loose` scale outright — the ninth axis to close, and the one where
    // closing it mostly meant DELETING classes rather than renaming them.
    //
    // The reason is that this app already has a body line-height and it is not written
    // anywhere: preflight sets `html { line-height: 1.5 }`, `body` inherits it, and the
    // form controls inherit it too. The `fontSize` rungs above are bare strings on
    // purpose, so no rung overrides it. Every element in the app is at 1.5 unless it
    // says otherwise — which is why there is no `leading-body` here. Sixteen sites used
    // to write `leading-[1.5]`, and all sixteen were restating the value they already
    // had; a rung for them would be a name with nothing to wear it, which is worse than
    // the one-wearer rung the dimension scales already refuse.
    //
    // Twenty-one more sites were spending 1.35, 1.375, 1.4, 1.45, 1.6 and 1.625 on ONE
    // job — a sentence the operator reads — and the spread was not a decision. The same
    // component wore two of them three separate times: the numbered how-to step is
    // copied into neurocomment, neuroshilling and warming and came out 1.5, 1.5 and
    // 1.45; the chat bubble is 1.5 in PreviewCard and 1.45 in DialogueFeed; `Notice`
    // draws itself at 1.5 in `shared/ui` and TwoFactorSection passes 1.45 back into it.
    // That is the argument `transitionTimingFunction` makes about its third curve, and
    // it is stronger here: there the two values met on one gesture, here they meet on
    // one component. At 12.5px, 0.05 of line-height is 0.625px — under a pixel, and no
    // site in the tree could have been leaning on it, because the app has no
    // `line-clamp` and not one of those sentences sits in a fixed-height box. All of
    // them are gone, back to the 1.5 they were already inheriting.
    //
    // What is left is the two places the app genuinely departs from its body leading,
    // each named for what wears it and each worn twice:
    //
    // `stack` is the heading line of a two-line row — a name with a detail line under
    // it. IdleBanner's amber label over its explanation, and the account name over its
    // phone in WarmingPage's graduated list. Both pull the second line up with `mt-px`,
    // and at 1.5 the heading's own leading undoes that; 1.25 is what makes the pair read
    // as one block. It absorbs `leading-tight`, which was this value under Tailwind's
    // name for it, so neither wearer moves a pixel.
    //
    // `log` is the monospace stream on a `term` surface: LogTerminal, and the embedded
    // log inside WarmingBoard's card. A log line is scanned, not read, and the air
    // between rows is what makes a row findable. Two components, 1.85 against 1.7 — the
    // same drift as above, so it takes the value of the one the other is a copy of.
    //
    // `none` is not a text rung and stays out of that count, the way `borderRadius.none`
    // does: it is the absence of leading, worn by things that are a single character
    // rather than a line of text — the `×` that removes a chip in five places,
    // HelpHint's `?`, and WarmDaysModal's one 42px numeral. The lint rule already reads
    // it as the marker that a class list is drawing a glyph, so it keeps Tailwind's name.
    lineHeight: {
      none: '1',
      stack: '1.25',
      log: '1.85',
    },
    // Not a scale, and the entry below is the exception that says why. Letter-spacing is
    // the one axis where the canon already had the answer before it was asked: the two
    // values the app spends on TYPE are declared by the roles that need them —
    // `eyebrow` carries 0.04em because caps at 11px close up without it, and
    // `page-title` carries -0.02em because 22px bold opens up with it. A `tracking-*`
    // rung for either would be a second way to say what a role already says, which is
    // the ninety-six-spellings failure the roles were introduced to end. The remaining
    // three literals were not rungs either: 0.03em on the warmed badge is the fifth
    // eyebrow the role's own note records as having drifted, -0.02em on WarmDaysModal's
    // numeral is `page-title`'s value on the one element too big to be a role, and
    // -0.01em on the wordmark is 0.16px per character on one mark rendered twice, which
    // is below every threshold this file has called drift. All five are gone.
    //
    // `code` is the one that survives, and it survives because it is not typography: it
    // is a FIELD's affordance, the spacing that lets a one-time code be read back
    // character by character while it is typed. Two independent wearers — the SMS login
    // code in SessionSection and the 2FA email code in TwoFactorEmail — and the app
    // already depends on it in prose: both fields pair it with a "1 2 3 4 5" placeholder,
    // and both trim the spaces that invites before Telegram sees them.
    letterSpacing: {
      code: '0.18em',
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
    //
    // These rungs reach the TRANSITIONS and nothing else. The app's animations are still
    // untokenised on both sides: index.css spends fourteen raw durations on its
    // `animation:` shorthands, and eight `[animation:…]` arbitrary utilities in five
    // components spend four more (0.2s, 0.25s, 0.3s and a 0.09s stagger) plus the bare
    // keyword `ease`. The lint rule bans `duration-[` and reaches none of it, which is
    // measured and left open rather than missed: this note is what was chosen over
    // closing it, so the next reader does not have to redo the sums.
    //
    // The eight inline ones are four gestures with two wearers each — an overlay fading
    // in (Modal, ProfileModal's syncing wash), a card arriving (Modal, Toaster), and the
    // two halves of a saved-state swap (CampaignPromptModal, ListenerEditModal) — and two
    // of the four already have a named class in index.css sitting 20ms away: `.tb-swapin`
    // at 0.32s against their 0.3s, `.tb-fadeup` at 0.4s against their 0.25s. Naming the
    // other two is cheap. Consolidating `fadeup` is not, and it is the whole cost: 0.25s
    // is `enter`, which this table defines as "something arriving (a toast, an opacity
    // fade-in)" and which therefore already agrees with the toast, while 0.4s is fifteen
    // page sections and cards, a rung nearer `reveal`. One keyframe, two rungs, two
    // readings — either fifteen elements get retimed to make one name true, or the canon
    // carries a second name for one gesture. That is a decision about how the product
    // should feel, not a sweep, and it is worth less than it costs today: the blanket
    // `prefers-reduced-motion` rule at the bottom of index.css already reaches every one
    // of these, so what is open here is tidiness, not behaviour.
    transitionDuration: {
      // `transitionDuration` and `transitionTimingFunction` sit at theme root, which
      // REPLACES Tailwind's scales — including their `DEFAULT` keys, which is not a
      // detail. Tailwind bakes those two defaults into every `transition-*` utility,
      // so replacing the scales without a DEFAULT emitted `.transition-colors` with a
      // `transition-property` and nothing else, and CSS's initial duration is 0s. The
      // commit that made these tokens authoritative is the commit that switched 25 of
      // the app's transitions off; six gates were green over it for a day, because a
      // transition that does not run is not a raw value, not a contrast failure and
      // not a drift between the config and the document. `motion.test.ts` compiles the
      // config and asserts a duration comes out, which is the only shape of check that
      // could have caught it.
      DEFAULT: '150ms',
      state: '150ms',
      enter: '250ms',
      reveal: '420ms',
      roll: '900ms',
    },
    // Two curves, replacing Tailwind's `ease-linear/in/out/in-out` outright — none of
    // which was used, and `out` deliberately takes the name `ease-out` so that the one
    // "settle" curve in the app is the one a component reaches for. `out` decelerates
    // hard and stops (a rail filling, a number rolling); `spring` overshoots slightly
    // and settles back (a panel opening, a chevron flipping).
    //
    // Three strays came home, not one, and the note that used to sit here claimed the
    // job was finished when it had covered a third of it. `cubic-bezier(.34,1.4,.6,1)`
    // was 0.05 of overshoot from `spring`, on the very panel whose chevron used
    // `spring`; that one went when the TRANSITION declarations in index.css were
    // tokenised. The file's `animation:` shorthands were never touched by that sweep and
    // kept two more: `.tb-blur` at `(.34,1.56,.64,1)` — the same overshoot-and-settle,
    // 9.78% past the target where `spring` goes 6.60%, which on a 52px avatar's
    // 0.78→1 scale is 1.1px of bounce against 0.75px — and `.tb-drawerin` at
    // `(.22,1,.36,1)`, a decelerate-and-stop with no overshoot at all, 9.6% of the
    // travel behind `out` at its widest and inside 1% of it by the halfway point.
    // Neither difference is a third intention; both are the same two gestures written
    // from memory, so both now name the rung they always meant. The claim and the
    // stylesheet agree again, and `index.test.ts` asserts a literal curve cannot come
    // back into that file.
    transitionTimingFunction: {
      // See the note on `transitionDuration.DEFAULT`: without this key every
      // `transition-*` utility loses its easing as well as its duration.
      DEFAULT: 'cubic-bezier(.16,1,.3,1)',
      out: 'cubic-bezier(.16,1,.3,1)',
      spring: 'cubic-bezier(.34,1.45,.6,1)',
    },
    // Six stacking layers, lowest to highest, so a component never has to guess a
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
        // The wash over an image that a control has to stay legible on — PhotoTab's
        // remove button over a profile photo. Dark ink at 55%, not a flat grey: what is
        // behind it is a photograph, and a solid fill would read as a hole punched in it.
        //
        // AddStoryModal's story preview does the same job with `bg-black/55` and is NOT
        // this token, which is a measured decision and not an omission. The two washes
        // differ by 11 units of warmth, 6 once 55% of them lands, invisible over any
        // photograph — but the worst case a photograph offers is a blown-out white
        // region, and there `bg-black/55` composites to #737373 and this to #797979,
        // which puts the white index numeral on the preview at 4.74:1 against 4.35:1.
        // Unifying the spelling would spend 0.39 of contrast to tidy a colour nobody can
        // see, and would push a numeral under the floor to do it. What that measurement
        // actually says is that 55% is marginal for white text at ALL — PhotoTab's own
        // control sits at the same 4.35:1 — so the open question is whether this rung
        // should be darker, which is a change to what the app looks like and belongs to
        // whoever owns that, not to a sweep.
        scrim: 'rgba(11,11,12,0.55)',
        // The other dark wash, and the pair is the point: `scrim` sits over ONE
        // photograph so a white control on top of it clears 4.5:1, `veil` sits over the
        // whole page so the page recedes behind the dialog that took it. Nothing is
        // painted on `veil` — the dialog card is opaque white on top of it — so it is
        // dimming, not contrast, and 40% is where the page reads as put away rather
        // than as removed.
        //
        // One wearer, which the canon otherwise treats as a literal with a name. It
        // earns the name the way `minWidth.table` does: what it replaces is worse than a
        // literal. `Modal` took an unbounded `backdrop?: number` and wrote
        // `rgba(11,11,12,${backdrop})` into the app's only inline style-object colour,
        // so every dialog in the app carried a continuous dimming knob no gate could
        // read. Four call sites had turned it to 0.45 — AddStoryModal, which is where
        // the design source's single 0.45 landed, plus ChannelCreateModal,
        // ChannelEditModal and NavDrawer, each of which copied AddStoryModal's whole
        // `<Modal>` line including its `z={75}` and its `w-[460px]`. That is one value
        // propagated by copy, not four dialogs asking for more dark; the twenty-two
        // others, ProfileModal and its photographs included, were already on 0.40. The
        // knob is gone with the prop.
        veil: 'rgba(11,11,12,0.40)',
        // The one step off white: a row under the pointer (`.tb-row:hover`), and the fill
        // of a box that invites something into it rather than holding something already
        // there — SessionSection's dashed import drop zone. That drop zone said
        // `bg-canvas/40`, which over the white card it sits on composites to #f9f9f8:
        // one unit from this, and a translucent restatement of a token that is already
        // "canvas, weakened" by definition.
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
          // Also the hover fill, and the fill of the SELECTED one in a set of cards or
          // tiles under a `border-primary`. `wash` was a second pale blue four units
          // lighter, which is a difference a flat area cannot show; the one place the two
          // ever met is WarmingBoard's pipeline panel inside its tinted card, where four
          // units never bought a visible edge in the first place.
          //
          // The selected-card job used to be spelled as an alpha instead — `bg-primary/…`
          // at 0.06 three times, 0.08 once and 5 once, five tiles doing one job across
          // four slices. Composited on the white they all sit on those are #f0f6ff,
          // #ebf3ff and #f2f7ff: within four units of this rung, the same margin the
          // paragraph above rejects a whole token for. They are this colour, written from
          // memory five times, and the spread between the first two is the same 0.02 of
          // nothing that the `wash` merge was about. Being opaque also fixes what
          // SurfHover had to work around — a 6% fill let the action buttons parked under
          // a campaign card show through it.
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
          //
          // It is also the ONLY blue that reads on `tint`, which is what settled
          // WarmingBoard's pause countdown. That countdown sits inside the tinted
          // activity strip beside a `text-primary-deep` label and was written
          // `text-primary/70` to sit behind it — 11px mono at 2.81:1, the worst pairing
          // in the app and invisible to `contrast.test.ts`, whose ink pattern stops at
          // the `/`. Nothing in the ramp both recedes and reads, so the countdown takes
          // this rung (6.18:1) and recedes by being mono and regular next to a
          // semibold label instead.
          deep: '#0052cc',
        },
        // `deep` and `press` mirror the amber and blue rungs: `deep` is the darkest green,
        // for the heading of a notice on a green surface (WarmingBoard's "прогрет" block,
        // where DEFAULT measures only 2.97:1 on `tint` and this measures 5.85:1), and
        // `press` is the filled green button's hover, exactly as `primary.press` is the
        // blue one's: a step DOWN from the fill it replaces. It used to be a step up —
        // lighter than `deep` — on the theory that it was `DEFAULT`'s hover, and it left
        // the white label on the app's one green button at 4.32:1, under the floor, with
        // that number written here as if it were the fix. The button is `deep` (6.62:1),
        // so its hover has to be darker than `deep`, not lighter: this measures 8.12:1
        // under white and sits 1.23 from `deep`, the same step `primary.press` is from
        // `primary`.
        success: {
          DEFAULT: '#12a150',
          deep: '#0b6b37',
          press: '#0a5c2f',
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
  plugins: [
    // One utility per role, emitted into the `components` layer so that a utility on
    // the same element still wins: `type-caption text-danger` is a caption in the error
    // colour, and `type-card-title font-bold` is a card title someone still has to argue
    // for. That ordering is the whole reason this is a plugin and not a `@apply` recipe.
    plugin(({ addComponents, theme }) => {
      type Role = { size: string; weight: string; ink: string; tracking?: string; caps?: string };
      const roles = theme('typeRole') as Record<string, Role>;
      addComponents(
        Object.fromEntries(
          Object.entries(roles).map(([name, role]) => [
            `.type-${name}`,
            {
              fontSize: theme(`fontSize.${role.size}`) as string,
              fontWeight: role.weight,
              // `ink` and `ink-subtle` are how the utility spells it; `colors` nests the
              // ramp, so the DEFAULT rung has to be said out loud on the way through.
              color: theme(
                `colors.${role.ink === 'ink' ? 'ink.DEFAULT' : role.ink.replace('-', '.')}`,
              ) as string,
              ...(role.tracking === undefined ? {} : { letterSpacing: role.tracking }),
              ...(role.caps === undefined ? {} : { textTransform: role.caps }),
            },
          ]),
        ),
      );
    }),
  ],
} satisfies Config;
