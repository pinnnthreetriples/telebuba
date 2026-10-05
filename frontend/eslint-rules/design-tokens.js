// The design system is a closed set — tailwind.config.ts names every colour, type
// rung, radius, elevation, motion rung, line-height, letter-spacing and unit of rhythm
// the UI has — and a closed set only stays closed if reopening it is an error rather
// than a habit.
//
// Every pattern below flags ZERO sites in the tree it landed on, which is the bar the
// repo's other custom rule set: a rule that has to be suppressed to pass is not a
// rule, it is a warning with extra steps.
//
// It reads string literals and template chunks anywhere, not only in `className`: a
// style constant hoisted to the top of a module is the same decision written
// somewhere else, and that is exactly where the drift used to hide (eight files held
// a copy of one field's classes under four different names).
//
// What this rule deliberately does NOT flag, and why:
//
//   `bg-white` / `text-white` (213 sites) — white and black are the two colours a
//   palette does not have to name. An alias would be a synonym rather than a role,
//   and there is no second theme for it to point somewhere else in. They are rungs of
//   `theme.colors` now rather than leftovers of Tailwind's palette underneath it, which
//   is what makes them nameable at all: the palette REPLACES Tailwind's, so a colour the
//   config does not carry does not compile.
//
//   an ALPHA modifier on white or black (13 sites) — `bg-white/85` under the nav bar's
//   blur, `bg-white/70` over a photo grid mid-drag, `bg-black/55` over a story preview,
//   `bg-black/10` over a syncing modal body, `border-black/5` and `border-white/40`
//   hairlines drawn on a photograph. Same
//   carve-out as the line above, and for a sharper reason: the palette holds flat
//   colours, and what is behind each of these is a photograph or a scrolling page, so
//   there is no composite for a token to be. The pattern below therefore bans an alpha
//   only on a colour the palette DOES name, which is the case where a composite exists
//   and something else already has its name. AddStoryModal's `bg-black/55` is the
//   closest any of the eleven comes to failing that test — it is `scrim` with 11 units
//   of warmth left out — and measuring it is what kept it out here: over the whitest
//   thing a photograph can be, the two washes put a white numeral at 4.74:1 and 4.35:1.
//   The token comment carries the working.
//
//   `#rrggbb` inside a style VALUE rather than a class (2 sites) — the two decorative
//   gradients above. The colour-function pattern below reaches `rgb()`/`hsl()` in a
//   string and deliberately stops short of bare hex, so those two keep the exemption
//   they already have and no new suppression is added to buy it.
//
//   `rounded-[1px|2px|3px]` (24 sites) — a hairline's radius. Snapping a 2px progress
//   bar or a chat bubble's tail up to the 8px rung would round it away, so the radius
//   pattern starts at 4px.
//
//   arbitrary spacing above 34px (10 sites) — the rhythm is dense from 4 to 32px and
//   that is the range the pattern covers. Above it are a page's own breathing room
//   and the room a control takes up inside a field, one-offs by nature.
//
//   a dimension measured against the viewport or the text (`w-[min(84vw,300px)]`,
//   `max-w-[90vw]`, `h-[1.1em]`) — those are not values the design system could hold,
//   because they resolve differently on every screen. Anything in px or rem is.
//
//   a dimension only one component ever asks for (12 sites) — AddStoryModal's
//   collage-layout tile and story preview, NeuroAccountsModal's spend gauge (its bars
//   and the row they stand in), Switch's track, AccountsTable's trust bar,
//   AccountsPage's search pill, WarmingBoard's embedded log, LoginPage's card,
//   SettingsPage's settings column, ProxyPool's empty-state sentence and ScenarioCard's
//   prompt column. A dimension owned by one component's internal layout is that
//   component's business, not the scale's: giving it a rung would put a name with a
//   single wearer in the canon, which is a literal with a name and the way a closed set
//   reopens. Each of the twelve carries its own inline suppression rather than a hole in
//   the pattern, so the second component to reach for the same measurement is flagged
//   and has to argue for a rung.
//
//   `leading-none` (8 sites) — a declared rung, and deliberately not a text one. Its
//   wearers are single characters, not lines: the `×` that removes a chip in five
//   places, HelpHint's `?`, and WarmDaysModal's 42px day count. It is also the marker
//   the type-role pattern below reads to tell a glyph from text, which is the second
//   reason it keeps Tailwind's name.
//
//   `leading-[1.1em]` (2 sites, Odometer) — the em carve-out, and the only one the
//   line-height pattern makes. It is the same argument the dimension pattern already
//   makes for `h-[1.1em]`, which is the class sitting beside it: this is a measurement
//   against the text, not a rung a design system could hold. Concretely it is a
//   geometric constant said in four places — the column's height, each digit cell's
//   height, this line-height, and the `translateY(-n * 1.1em)` that rolls the digit into
//   place. Round the line-height to a typographic rung and the digits stop landing. The
//   two sites used to spell it `[1.1]` and `[1.1em]`; they are one spelling now, so the
//   carve-out has one shape to allow rather than two.
//
//   `tracking-[…]` inside `shared/ui` — the same `above` carve-out the type-style
//   pattern makes, for the same reason: `shared/ui` is the layer allowed to compose
//   primitives by hand. No site in it spends one today; the carve-out stays so the layer
//   that IS the design system is not asked to argue with its own rule.
//
//   two decorative gradients (ProfileModal, _profileShared) — placeholder fills
//   behind an avatar or a thumbnail that has not loaded. They exist only to differ
//   from each other; naming them would put two single-use roles in the canon and
//   imply the UI means something by them. Both carry an inline suppression.

// The type-role pattern below is the one rule here that does not apply everywhere, and
// the exception is the point rather than a hole. `shared/ui` is the layer allowed to
// compose primitives by hand: `Button` deciding that its label is 13px semibold IS the
// design system, said in the place the system is kept. Every layer above it —
// `pages/`, `widgets/`, `features/`, `entities/`, `routes/` — is a consumer, and a
// consumer respelling a rung, a weight and a grey is how one job came to have three
// spellings. Above `shared/ui` a page names the ROLE the text plays.
// A test file is exempt too, and for the opposite reason: `cn.test.ts` and
// `designTokenRule.test.ts` assert on the very spellings this bans, and a fixture is
// data about the code rather than a decision inside it.
import { colorRoots, scale, scaleNames } from '../scripts/configScales.mjs';

// The composition of every scale comes from `src/shared/design-system/tokens` — the same
// objects `ds:dead`, the doc generator, `cn.ts` and Tailwind itself read. That is the
// point: the gate that bans a value outside the scale and the gate that bans a scale rung
// nobody wears can no longer disagree about what the scale IS.

const NOT_A_CONSUMER = /(?:^|[\\/])src[\\/]shared[\\/]ui[\\/]|\.test\.tsx?$/;

// The token modules themselves: the one place a raw value is the correct thing to write.
const IS_THE_SYSTEM = /(?:^|[\\/])src[\\/]shared[\\/]design-system[\\/]tokens[\\/]/;

//
// The rest of the set is READ from the config rather than spelled here. It used to be
// spelled, with the note that "a name that leaves this file is a rename the sweep has to
// notice anyway" — which is true of a rename and false of an ADDITION, and an addition is
// what actually happens: a ninth rung or a twelfth colour lands in the config, this list
// does not grow, and the pattern below quietly stops covering it. A gate that goes green
// by looking at less is the failure mode the design system's own gates exist to catch.
const TYPE_RUNG = scaleNames('fontSize').join('|');
const INK_RAMP = String.raw`text-content-(?:primary|secondary|muted|subtle)(?![\w-])`;

// What this pattern deliberately does NOT reach, and why:
//
//   a class list that also paints a box — a fill, a border, a radius, an elevation or a
//   focus ring. That is a CONTROL being drawn, and a control's face is its own business:
//   a status pill, a hand-written button, a glyph badge, a field. Those belong to
//   `Badge`, `Button` and `Input`, and the ~100 drawn by hand above `shared/ui` are a
//   layering debt to pay by moving them down — not by inventing `type-pill` and
//   `type-control`, which would put the debt in the canon and call it design.
//
//   Padding used to be on that list and is not any more. It was there because a control
//   pads its own label, but padding paints nothing: it is the commonest utility in the
//   app, so `p-*` alone exempted 18 class lists that draw no box at all. Every one was a
//   page's empty, loading or error state — a centred sentence in a padded gap — written
//   across three rungs (`lead` eight times, `body` nine, `micro` once) and three greys.
//   That is the exact drift this pattern exists to stop, let through by the one prefix
//   that says nothing about whether a box is being drawn.
//
//   a class list that reacts to the pointer (`hover:`, `focus`, `active:`, `disabled:`,
//   `transition`, `cursor-`). Same category, reached from the other side: three of them
//   draw no box but are still controls — a text button, a nav tab, a tooltip trigger.
//
//   a class list carrying `leading-none`, `absolute` or `fixed`. At `lead` the type
//   scale doubles as a GLYPH size, exactly the way `IconButton` wears `text-h3` to
//   size a `×`: all six such sites in the tree are one character — the `×` that removes
//   a chip in CreateCampaignModal, CampaignsCard and WarmingPage, and the `@` prefix
//   inside the username fields of ChannelCreateModal and ProfileModal. A glyph is not
//   text playing a role, and no `type-*` should pretend it is.
//
//   a rung beside a WEIGHT with no grey in the list. Measured, not assumed: that
//   variant flags seven sites no role can honestly absorb, and every one of them is a
//   number — a trust score, a spend gauge, a tile's figure, an avatar's initials —
//   where the weight is the figure's emphasis and not a heading's. A pattern cannot
//   tell a bold heading from a bold number, so the weight half of the canon is carried
//   by the role table and its documentation rather than by this gate.
const PAINTS_A_BOX = String.raw`(?:^|\s)(?:[\w-]+:)*(?:bg-|border(?![\w-])|border-|rounded|shadow-|ring-)`;
const IS_A_CONTROL = String.raw`(?:^|\s)(?:hover|focus|focus-visible|focus-within|active|disabled|aria-[\w-]+|data-[\w-]+):|(?:^|\s)transition|(?:^|\s)cursor-`;
const IS_A_GLYPH = String.raw`(?:^|\s)(?:leading-none|absolute|fixed)(?![\w-])`;

// Tailwind's own palette, and the pattern it feeds changed job. While the app's colours
// sat in `theme.extend`, `bg-blue-500` compiled and painted a blue nobody chose, and this
// pattern was the only thing standing between the two palettes — inside `src`, outside a
// test file. The palette is at `theme.colors` now, so the class emits NOTHING and the
// element silently keeps what it inherited; the pattern stays for the reason
// `RETIRED_LEADING` does, to name a class that does nothing rather than let it look right.
//
// A literal list rather than a read of the config, unlike `TOKEN` and `TYPE_RUNG`: these
// are not this design system's names, and nothing in this repo renames them.
const PALETTE =
  'slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose';
// Направленные утилиты перечислены наравне с общими, и это не полнота ради полноты:
// `border-white` правило видело, а `border-t-white` — нет, и кольцо ожидания красило дугу
// именно им четыре раза. Направление — не другая краска.
const COLOUR =
  'bg|text|border|border-x|border-y|border-t|border-r|border-b|border-l|' +
  'ring|ring-offset|fill|stroke|from|to|via|divide|divide-x|divide-y|' +
  'outline|decoration|caret|accent|shadow|placeholder';
const SPACE = 'p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|gap-x|gap-y|space-x|space-y';
// Всё, что читает `theme.spacing`, а не только поля и зазоры: `top-lg` и `scroll-mt-page`
// после перехода на сетку молчат точно так же, как `p-md`.
const SPACE_ALL =
  `${SPACE}|ps|pe|ms|me|inset|inset-x|inset-y|top|right|bottom|left|start|end|` +
  'translate-x|translate-y|scroll-m[xytrbl]?|scroll-p[xytrbl]?|indent';
// Ступени ритма — из токенов, как и всё остальное здесь. `0` и `px` стоят в той же
// шкале, но ступенями не считаются и в подсказке не называются.
const RHYTHM = scale('spacing');
const RHYTHM_NUMERIC = Object.keys(RHYTHM).filter((name) => /^\d+$/.test(name));
const RHYTHM_RUNGS = RHYTHM_NUMERIC.filter((name) => name !== '0')
  .map((name) => `\`${name}\` ${RHYTHM[name]}`)
  .join(', ');
// Имена, которые ритм носил до сетки Firecrawl. Перечислены по той же причине, что
// `RETIRED`: незнакомая утилита не выпускает правила, и `gap-md` после перехода — не
// ошибка сборки, а класс, который молча ничего не делает.
const RETIRED_RHYTHM = 'hair|xs|tight|sm|md|lg|xl|2xl|page|empty';
const DIMENSION = 'size|min-w|max-w|min-h|max-h|w|h';

// A utility class starts at the beginning of the string or after whitespace. Anchored
// so `sub-p-2` or a URL that happens to contain `to-3` is not a hit.
const at = (body) => new RegExp(String.raw`(?:^|\s)(?:${body})`);

// Colour names the canon collapsed into another. Listed rather than left to fail on
// its own, because only half of them fail visibly: an unknown `bg-*` emits no rule at
// all and the chip loses its fill, which anyone reviewing the screen sees — but an
// unknown `border-*` on an element that also carries `border` falls through to
// preflight's own default, Tailwind's `gray-200`, three units from `line` and cool
// where the app is warm. That one comes back looking right.
const RETIRED =
  'track|line-input|primary-wash|success-dot|' +
  // The names the semantic pass retired. Listed for the reason the four above are: an
  // unknown `bg-*` emits nothing and the element loses its fill, which a reviewer sees —
  // but an unknown `border-*` on an element that also carries `border` falls through to
  // preflight's own `gray-200`, three units from `line` and cool where the app is warm.
  // That one comes back looking almost right.
  //
  // Each of these was one colour doing two jobs, which is why they went: `white` was a
  // card's fill AND a filled button's label, `primary` was an action's fill AND a link's
  // ink AND the dark blue that reads on a tint, `ink` was named after the material rather
  // than the role.
  'ink|ink-body|ink-muted|ink-subtle|' +
  'primary|primary-press|primary-tint|primary-deep|primary-line|primary-hairline';

// Tailwind's own line-height and letter-spacing names, which this config replaces
// outright the way it replaced the type scale. They are listed rather than left to fail
// on their own for the reason the retired colours are: an unknown utility emits no rule
// at all, so `leading-relaxed` after this change is not an error, it is a class that
// silently does nothing and leaves the element at whatever it inherited. Half of these
// were in the tree — `snug` on three explanations, `relaxed` on two, `tight` on an
// account name — and none of them would have announced its own removal.
const RETIRED_LEADING = '10|3|4|5|6|7|8|9|tight|snug|normal|relaxed|loose';
const RETIRED_TRACKING = 'tighter|tight|normal|wide|wider|widest';

// The type scale that the five steps replaced, and Tailwind's own weights beyond the two
// this app keeps. Listed for the reason the retired colours are: an unknown utility emits
// no rule, so `type-caption` or `font-bold` after the rebuild is not an error, it is a
// class that silently does nothing.
const RETIRED_TYPE_STEP = 'tiny|title|stat|display|hero|lead|micro';
const RETIRED_TYPE_ROLE =
  'page-title|dialog-title|dialog-body|card-title|item-title|eyebrow|label|value|prose|caption|table-header|stat|meta';
const RETIRED_TYPE_LEADING = 'stack|log|body';
const RETIRED_WEIGHT = 'thin|extralight|light|semibold|bold|extrabold|black';

// The palette's own names, as a class list spells them — the roots only, since a rung
// (`primary-tint`, `ink-subtle`) is reached by the optional tail in the pattern. Read
// from the config for the reason given at `TYPE_RUNG`.
//
// `white` and `black` come out: the pattern this feeds bans an alpha modifier on a named
// colour, and alpha on those two is the documented exception — `border-black/5` on a
// photograph, `bg-black/55` on a story tile. `transparent` and `current` come out because
// an alpha on a keyword is not a colour the palette failed to name; it is nonsense that
// emits nothing.
const NOT_A_TINTABLE_COLOUR = new Set(['white', 'black', 'transparent', 'current']);
const TOKEN = colorRoots()
  .filter((name) => !NOT_A_TINTABLE_COLOUR.has(name))
  .join('|');

const PATTERNS = [
  {
    test: at(String.raw`(?:[\w-]+:)*(?:${COLOUR})-(?:${RETIRED})(?![\w-])`),
    message:
      'That colour was collapsed into another one and no longer exists: `track` and `primary-wash` are `canvas` and `primary-tint`, `line-input` is `line`, `success-dot` is `success`. The unification ledger in docs/design-system.html carries the reason for each.',
  },
  {
    // `bg-white` / `text-white` without an alpha: both were one class doing two jobs, and
    // the split is the whole point of the semantic pass — a card's fill is
    // `bg-surface-card`, a filled action's label is `text-on-action`, ink on the dark
    // surface is `text-on-inverse`. WITH an alpha they stay legal: white at 85% under the
    // nav's blur and white at 40% as a hairline on a photograph are the extreme of the
    // range rather than a role, and there is no flat composite for them to be.
    // Прежний список знал `border-` и не знал `border-t-`, поэтому `border-t-white`
    // проходил насквозь — им была набрана дуга кольца ожидания в четырёх местах. Это
    // закрыто: направление не другая краска.
    //
    // `stroke` и `fill` были исключены ЦЕЛИКОМ, и причина была измеренная: `stroke-white`
    // стоял в восьми местах — белая галочка на ЗАЛИТОМ контроле, тот же дефект, что у
    // кольца, — но роли «чернила на залитом тоне» в системе не было, а надеть `on-action`
    // на успех значило бы соврать именем. Правило, которое надо шесть раз подавить, чтобы
    // оно прошло, — не правило, поэтому исключалась приставка, а не сайты.
    //
    // Роли появились (`on-success`, `on-warning`, `on-danger`), все восемь мест на них
    // перешли, и исключение снято: список приставок здесь снова тот же `COLOUR`.
    test: at(String.raw`(?:${COLOUR})-(?:white|black)(?![\w-/])`),
    message:
      'Bare `white`/`black` is a colour doing two jobs. A white surface is `bg-surface-card`; the label on a filled action is `text-on-action`; ink on a filled feedback tone is `on-success`/`on-warning`/`on-danger`; ink on a filled NEUTRAL (the ink-dark button, the grey counter) is `text-on-neutral`; ink on the dark surface (a toast, a tooltip, a scrim over a photograph) is `text-on-inverse`; the muted ink ON a filled action (a waiting ring’s track) is `on-action-track`. An alpha form — `bg-white/85`, `border-black/5` — stays legal ONLY where what is behind it is a photograph or a scrolling page, so no flat composite exists for it to be: over a known flat fill the composite exists and has a name.',
  },
  {
    // Индикатор фокуса краской ДЕЙСТВИЯ. `border.focus` был объявлен ступенью с самого
    // начала и не доходил ни до одного класса: восемь контролов рисовали фокус через
    // `outline-action-primary`, поэтому перекрасить кнопку означало перекрасить фокус.
    // Значение у них одно и остаётся одним — разъединены имена, и это правило держит
    // разъединение, потому что классы выглядят одинаково работающими.
    test: at(
      String.raw`focus(?:-visible|-within)?:(?:outline|border|shadow|ring)-action-(?:primary|hover|pressed)(?![\w-])`,
    ),
    message:
      'A focus indicator painted with the ACTION colour ties the two together: recolouring the buttons would recolour the focus ring. They are one value and two decisions — use `outline-focus`, `border-focus` or `shadow-focus`.',
  },
  {
    // Кольцо ожидания, собранное руками. Оно было собрано так семнадцать раз, и дорожка
    // разошлась на два серых (`line` в семи местах, `line-strong` в пяти) — то есть
    // повторение строки не осталось повторением. `tb-spin` САМ ПО СЕБЕ законен: им же
    // крутится иконка обновления в ProfileModal, и это не кольцо. Ищется именно кольцо:
    // анимация вместе с окрашенной верхней границей.
    // Порядок классов в строке не гарантирован, поэтому совпадение с проверкой второй
    // половины через опережение: `hit[0]` при этом остаётся осмысленным для сообщения.
    test: /border-t-[a-z][\w-]*(?=[\s\S]*tb-spin)|tb-spin(?=[\s\S]*border-t-[a-z])/,
    // Кроме самого компонента: он и есть то место, где кольцо собрано.
    above: /[\\/]Spinner\.tsx$/,
    message:
      'A waiting ring assembled by hand. `Spinner` is the component: `size` is `sm`/`md`/`lg` and `tone` is `default`/`inverse`/`danger`. Seventeen copies of these classes drifted into two different track greys, and four call sites set the size in raw pixels 12–15px apart.',
  },
  {
    // Брейкпоинт, которого нет. Шкала `screens` тоже закрыта — три ступени, которые
    // приложение носит, — и `xl:`/`2xl:` теперь не выпускают НИ ОДНОГО правила: класс
    // выглядит работающим и молчит. Ровно тот же дефект, что у палитры Tailwind рядом.
    test: at(String.raw`(?:xl|2xl):[a-z]`),
    message:
      "The breakpoint scale is closed at three rungs — `sm` (640), `md` (768), `lg` (1024) — and `theme.screens` REPLACES Tailwind's, so `xl:`/`2xl:` emit no rule at all and the element silently keeps the layout it had. The numbers live in `breakpoint` in the token tree, which `useWideViewport.ts` reads too; add a rung there if the layout genuinely needs a fourth.",
  },
  {
    test: at(String.raw`(?:${COLOUR})-(?:${PALETTE})-\d{2,3}(?![\w-])`),
    message:
      "Tailwind's own palette is not this app's, and the config no longer keeps it reachable: `theme.colors` REPLACES it, so this class emits no rule at all and the element silently keeps whatever colour it inherited. Use the semantic colour.",
  },
  {
    test: at(
      String.raw`(?:bg|text|border|ring|fill|stroke|from|to|via|shadow)-\[(?:#|rgb|hsl|oklch)`,
    ),
    message:
      'A colour written into a class is a colour the design system does not know about. Name it in tailwind.config.ts — every colour there carries its role and, where it is text, its measured contrast — and use that name.',
  },
  {
    test: at(
      String.raw`(?:[\w-]+:)*(?:${COLOUR})-(?:${TOKEN})(?:-[a-z]+)?/(?:\[[0-9.]+\]|\d{1,3})(?![\w-])`,
    ),
    message:
      'An alpha modifier on a named colour paints a colour the palette does not name, and the palette cannot see it: `contrast.test.ts` reads a token per class and its ink pattern stops at the `/`. Seven sites wrote one this way and every one already had a name — five selected cards and tiles spelled `bg-primary` at 0.06, 0.08 and 5 across four slices, all of them `bg-primary-tint` to within four units on the white they sit on; a drop zone spelled `bg-canvas/40`, which is `bg-surface` to within one; and a countdown spelled `text-primary/70`, which measured 2.81:1 on the tint it sits in. Alpha on `white` or `black` is the exception, and the header says why. Name the composite, or use the token that already is it.',
  },
  {
    test: /(?:rgba?|hsla?)\(/,
    message:
      'A CSS colour function in a string is a colour computed at the call site, which is where the modal backdrop lived: an unbounded `backdrop?: number` composed into `rgba(11,11,12,${n})` on the app’s only inline style-object colour, so twenty-two dialogs carried a continuous dimming knob no gate could read. A wash over the page is `bg-veil` and a wash over a photograph is `bg-scrim`; both are in tailwind.config.ts with the alpha they were argued down to.',
  },
  {
    test: at(
      String.raw`(?:[\w-]+:)*(?:text-(?:${RETIRED_TYPE_STEP})|type-(?:${RETIRED_TYPE_ROLE})|leading-(?:${RETIRED_TYPE_LEADING}))(?![\w-])`,
    ),
    message:
      'The type scale was rebuilt into five steps and seven styles, and this name went with the old one: it emits no rule, so the text silently falls back to whatever it inherits. Steps: `tiny` → `text-small`, `title` → `text-h3`, `stat`/`display`/`hero` → `text-h1`. Styles: `page-title`/`stat` → `type-h1`, `dialog-title` → `type-h2`, `card-title` → `type-h3`, `item-title`/`label` → `type-body-medium`, `prose`/`value`/`dialog-body` → `type-body` (plus its colour), `caption` → `type-small`, `eyebrow`/`table-header` → `type-small-medium`. `leading-stack`/`leading-log` are gone — line-height comes with the step.',
  },
  {
    test: at(String.raw`(?:[\w-]+:)*font-(?:${RETIRED_WEIGHT})(?![\w-])`),
    message:
      'Two weights, and the scale replaces Tailwind’s outright: `font-normal` (400) for what is read, `font-medium` (500) for what names something — a heading, an item, a field label, a button. Hierarchy is carried by the type step, not by boldness, so `semibold`/`bold` emit no rule here and the text silently stays at the weight it inherited.',
  },
  {
    test: at(String.raw`text-\[[0-9.]+(?:px|rem|em)\]`),
    message:
      'The type scale is closed: five steps — `text-small` 12, `text-body` 14, `text-h3` 16, `text-h2` 20, `text-h1` 24 — each carrying its own line-height and tracking, replacing Tailwind’s outright. A sixth size written in pixels is the drift those steps were introduced to end.',
  },
  {
    test: at(String.raw`(?:[\w-]+:)*-?(?:${SPACE_ALL})-(?:${RETIRED_RHYTHM})(?![\w-])`),
    message:
      'The rhythm is Firecrawl’s 4px grid with numeric keys now, and the named rungs are gone: this class emits no rule, so the element silently loses its spacing. `xs` → `1`, `sm` → `2`, `md` → `3`, `lg` → `4`, `2xl` → `6`, `page` → `8`, `empty` → `16`. `hair` (2px), `tight` (6px) and `xl` (20px) had no place on the grid and were mapped by role: `hair` → `1`, or `px` on a chip or between chart bars; `tight` → `1` inside a small control or between an icon and its label, `2` between lines; `xl` → `6` for a card’s or dialog’s padding, `4` for a button’s or between groups. docs/design-system.md, «Седьмой проход», carries the table.',
  },
  {
    // Числовые ключи теперь законны — но только объявленные. `p-5` и `gap-0.5` есть в
    // шкале Tailwind и нет в нашей, а шкала ЗАМЕНЕНА: класс молчит, как и старое имя.
    test: at(
      String.raw`(?:[\w-]+:)*-?(?:${SPACE_ALL})-(?!(?:${RHYTHM_NUMERIC.join('|')})(?![\w.]))\d[\d.]*(?![\w./[])`,
    ),
    message: `The rhythm is a closed 4px grid, and only the rungs the app wears are declared: ${RHYTHM_RUNGS}, plus \`0\` and the \`px\` hairline. Tailwind’s own numeric scale is replaced, so an undeclared step emits no rule and the element silently loses its spacing. Use the nearest rung; a rung nobody wears is rejected by \`ds:dead\`, so a new one lands together with its wearer.`,
  },
  {
    test: at(String.raw`(?:${SPACE})-\[(?:[0-9]|[12][0-9]|3[0-4])px\]`),
    message: `The rhythm has a rung near this value: ${RHYTHM_RUNGS}. Reach for it — a measurement in pixels beside a 4px grid is where two rhythms start again.`,
  },
  {
    test: at(String.raw`(?:[\w-]+:)*(?:${DIMENSION})-\[[0-9.]+(?:px|rem)\](?![\w-])`),
    message:
      'Dimensions are their own scale now: `size-*` for a square, `width`/`height` for everything else, and each rung is named for the component that wears it. This rule used to exempt `w-*`/`h-*` in pixels on the grounds that a component’s size is not a rung of the rhythm — which was true, and is exactly how 73 distinct dimensions grew beside eleven rungs. Both halves are scales now, so a measurement here belongs in one of them.',
  },
  {
    test: at(String.raw`(?:[\w-]+:)*rounded(?:-[a-z]{1,2})?-card(?![\w-])`),
    message:
      'The radius scale is Firecrawl’s now and `card` is gone: this class emits no rule, so the card silently loses its corners. The card and the dialog are `rounded-lg` (16px). The rename was one pass, never two: the old `lg` (11px) is `rounded-md` (12px), the old `md` (8px) and `sm` (6px) are both `rounded-sm` (8px).',
  },
  {
    test: at(String.raw`rounded(?:-[a-z]+)?-\[(?:[4-9]|[1-9][0-9])`),
    message:
      'Three radii and two shapes, on Firecrawl’s scale: `sm` 8px — an everyday control, a chip, a small tile; `md` 12px — a field, a panel, a menu, a card nested in a card; `lg` 16px — the card and the dialog; `full` — the pill and the avatar; and `none`. Hairlines under 4px keep their own value; anything larger has a rung.',
  },
  {
    test: at(String.raw`duration-\[`),
    message:
      'Four motion rungs, one per kind of gesture (`state`, `enter`, `reveal`, `roll`). A duration in milliseconds is how one gesture came to run 420ms on one element against 400ms on the other.',
  },
  {
    test: at(String.raw`(?:[\w-]+:)*leading-\[[0-9.]+(?:px|rem)?\](?![\w-])`),
    message:
      'Line-height belongs to the type step: `text-body` is 14px on 20px, `text-small` 12px on 16px, every one a multiple of 4. A sentence written with `leading-[…]` restates or fights the step it already wears. Delete the class; `leading-none` is the one rung left, for a single glyph.',
  },
  {
    test: at(String.raw`(?:[\w-]+:)*leading-(?:${RETIRED_LEADING})(?![\w-])`),
    message:
      'Tailwind’s line-height scale is replaced, so this name no longer emits a rule — the element silently keeps whatever it inherited rather than failing visibly. Line-height comes with the type step (`text-*`, `type-*`); `leading-none` is the one rung left, for a single glyph.',
  },
  {
    above: NOT_A_CONSUMER,
    test: at(String.raw`(?:[\w-]+:)*tracking-\[[^\]]*\]`),
    message:
      'Letter-spacing is not a scale in this app: the optical tracking of each type step is part of the step (`text-h1` carries -0.019em, `text-body` -0.006em, after Inter’s own Dynamic Metrics), and a `tracking-*` rung would be a second way to say what the step already says. `tracking-code` is the one name, and it is a field’s affordance rather than typography: the spacing that lets a one-time code be read back character by character as it is typed.',
  },
  {
    test: at(String.raw`(?:[\w-]+:)*tracking-(?:${RETIRED_TRACKING})(?![\w-])`),
    message:
      'Tailwind’s letter-spacing scale is replaced, so this name emits nothing and the element silently keeps the spacing it inherited. `tracking-code` is the only rung; type’s own spacing belongs to the type steps.',
  },
  {
    above: NOT_A_CONSUMER,
    unless: new RegExp(`${PAINTS_A_BOX}|${IS_A_CONTROL}|${IS_A_GLYPH}`),
    test: new RegExp(
      // A rung and a grey from the ink ramp, in either order, anywhere in one class list.
      String.raw`(?:^|\s)(?:[\w-]+:)*text-(?:${TYPE_RUNG})(?![\w-])[\s\S]*(?:^|\s)(?:[\w-]+:)*${INK_RAMP}` +
        String.raw`|(?:^|\s)(?:[\w-]+:)*${INK_RAMP}[\s\S]*(?:^|\s)(?:[\w-]+:)*text-(?:${TYPE_RUNG})(?![\w-])`,
    ),
    message:
      'A step plus a grey is a style spelled out, and spelling it out is how one job came to have three spellings: the same small caption was once written `content-subtle` 53 times, `content-muted` 13 times, and with no colour at all 9 times. Above `shared/ui` the page names the style instead: `type-h1`, `type-h2`, `type-h3`, `type-body`, `type-body-medium`, `type-small`, `type-small-medium`, declared as `typeRole` in src/shared/design-system/tokens/typography.ts. A style plus a colour — `type-small text-danger`, `type-body text-content-subtle` — is the intended way to say the same text in another colour.',
  },
];

/** @type {import('eslint').Rule.RuleModule} */
const noRawValues = {
  meta: {
    type: 'problem',
    docs: { description: 'Design values come from the design system, not from the call site.' },
    schema: [],
  },
  create(context) {
    const filename = context.filename ?? context.getFilename();
    // The token modules are where a value is SUPPOSED to be written, so the whole rule is
    // off inside them. Not a hole and not a suppression: this rule's proposition is "a
    // value belongs to the design system, not to the call site", and these files are the
    // design system. Exempting them is the same move as `NOT_A_CONSUMER` exempting
    // `shared/ui` from the type-role pattern, one level down — and it is scoped to
    // `tokens/`, not to `design-system/`, so a recipe next door still cannot write a hex.
    if (IS_THE_SYSTEM.test(filename)) return {};
    const check = (node, text) => {
      if (typeof text !== 'string' || text.length === 0) return;
      for (const { test, message, above, unless } of PATTERNS) {
        if (above !== undefined && above.test(filename)) continue;
        if (unless !== undefined && unless.test(text)) continue;
        const hit = test.exec(text);
        if (hit) {
          context.report({ node, message: `${message}\n  found: ${hit[0].trim()}` });
          return;
        }
      }
    };
    return {
      Literal: (node) => {
        check(node, node.value);
      },
      TemplateElement: (node) => {
        check(node, node.value.raw);
      },
    };
  },
};

export default { rules: { 'no-raw-values': noRawValues } };
