import { expect, test } from 'vitest';

import config from '../../../tailwind.config';

// WCAG 2.1 AA asks 4.5:1 of text under 18.66px bold / 24px regular. Every type rung
// this app has is under that, so 4.5:1 is the floor for all of them — there is no
// "large text" exception to lean on here. 1.4.11 asks 3:1 of a graphic that carries
// meaning, which is what an element holding nothing but an icon is.
const AA = 4.5;
const NON_TEXT = 3;

type Ramp = Record<string, string> & { DEFAULT?: string };
const colors = config.theme?.colors as Record<string, string | Ramp>;

// The palette as a class list spells it: `content-primary`, `content-subtle`, `info-tint`.
// The
// config nests the ramps, so DEFAULT loses its rung on the way out. Anything that is
// not a flat hex has no ratio to measure — `scrim` is an rgba wash over a photograph,
// and `transparent`/`current` are keywords rather than colours.
//
// `white` used to be seeded here by hand, because the palette lived in `theme.extend`
// and white was Tailwind's. Now that the palette REPLACES Tailwind's it carries its own
// white, and this table reads it like every other rung — which is the point of the move:
// a colour the app paints and a colour this gate measures can no longer be two sets.
const HEX: Record<string, string> = {};
for (const [name, value] of Object.entries(colors)) {
  if (typeof value === 'string') {
    if (value.startsWith('#')) HEX[name] = value;
    continue;
  }
  for (const [rung, hex] of Object.entries(value)) {
    if (hex.startsWith('#')) HEX[rung === 'DEFAULT' ? name : `${name}-${rung}`] = hex;
  }
}

// A `type-*` utility carries its own ink, so a role is a colour decision even where no
// `text-*` class is written. Read off the config so the two cannot drift.
const ROLE_INK: Record<string, string> = Object.fromEntries(
  Object.entries(config.theme?.typeRole as Record<string, { ink: string }>).map(([name, role]) => [
    name,
    role.ink,
  ]),
);

function channel(byte: number): number {
  const c = byte / 255;
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function luminance(hex: string): number {
  const value = hex.replace('#', '');
  const [r, g, b] = [0, 2, 4].map((i) => channel(parseInt(value.slice(i, i + 2), 16))) as [
    number,
    number,
    number,
  ];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(text: string, background: string): number {
  const ink = HEX[text];
  const fill = HEX[background];
  if (ink === undefined || fill === undefined) {
    throw new Error(`no such colour token: ${text} / ${background}`);
  }
  const [a, b] = [luminance(ink), luminance(fill)];
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

// The pairings that used to ship: each one is a real fill/text combination that
// measured under the floor, and the reason the `deep` rungs exist. Asserting they
// still fail is what stops the fix from being quietly reverted by "restoring" the
// brighter colour — the token would go back to a value this test rejects.
test('the rungs that failed are the ones the deep rungs replaced', () => {
  expect(ratio('success', 'success-tint')).toBeLessThan(AA);
  expect(ratio('warning', 'warning-tint')).toBeLessThan(AA);
  // Базовый синий на тоне «в работе»: 4.38:1. Носит его `action-primary`, потому что
  // после переезда на роли у тона нет безрунговой ступени — она была синонимом заливки.
  expect(ratio('action-primary', 'info-tint')).toBeLessThan(AA);
  expect(ratio('danger', 'danger-tint')).toBeLessThan(AA);
});

// ---------------------------------------------------------------------------
// Reading the pairings off the source rather than listing them.
//
// The list used to be written by hand, under a comment claiming "a pairing that is not
// here is not painted anywhere". Nothing enforced that and it was not true: it carried
// `white on success.deep` and not `white on success.press`, which shipped on the promote
// button at 4.32:1 — a number the config itself printed as if it were the fix. It also
// carried two pairings nothing paints any more. So the table is derived now, and the
// only thing left by hand is the one kind of pairing a class scan structurally cannot
// see (below).
//
// The scan this replaces read one LINE at a time and asked whether it held both
// `bg-{tone}-tint` and that tone's failing text rung. Three things were invisible to it:
//   - a fill on a container and the text INSIDE it, which is the ordinary way a tinted
//     panel is written and how WarmingBoard's card painted `text-action-primary` on
//     `bg-info-tint` at 4.38:1 in three places;
//   - a state: `hover:bg-*` and the label it lands under are one class list but not one
//     pairing a per-tone scan looks for;
//   - every fill that is not a `tint` — `bg-*-line` is used as a fill twice.
// ---------------------------------------------------------------------------

type Chunk = { text: string; at: number; always: boolean };

// One chunk is one set of classes that always ship together. A template literal's
// static parts are one chunk; each string inside an interpolation is its own, and is
// NOT always applied — two branches of a ternary never paint at the same time, so
// pairing one branch's fill with another's ink invents a combination nothing renders.
// Comments are skipped: an apostrophe in a prose comment opens a string that runs to
// the next one and swallows whatever class lists lie between.
function chunks(src: string, from = 0, to = src.length, always = true): Chunk[] {
  const out: Chunk[] = [];
  let i = from;
  while (i < to) {
    const c = src[i];
    if (c === '/' && src[i + 1] === '/') {
      const nl = src.indexOf('\n', i);
      if (nl === -1) break;
      i = nl;
    } else if (c === '/' && src[i + 1] === '*') {
      const close = src.indexOf('*/', i + 2);
      i = close === -1 ? to : close + 2;
    } else if (c === '"' || c === "'") {
      let j = i + 1;
      while (j < to && src[j] !== c && src[j] !== '\n') j += src[j] === '\\' ? 2 : 1;
      out.push({ text: src.slice(i + 1, j), at: i, always });
      i = j + 1;
    } else if (c === '`') {
      let j = i + 1;
      let statics = '';
      while (j < to && src[j] !== '`') {
        if (src[j] === '\\') {
          j += 2;
          continue;
        }
        if (src[j] === '$' && src[j + 1] === '{') {
          let depth = 1;
          let k = j + 2;
          while (k < to && depth > 0) {
            if (src[k] === '{') depth += 1;
            else if (src[k] === '}') depth -= 1;
            k += 1;
          }
          out.push(...chunks(src, j + 2, k - 1, false));
          j = k;
          continue;
        }
        statics += src[j];
        j += 1;
      }
      out.push({ text: statics, at: i, always });
      i = j + 1;
    } else {
      i += 1;
    }
  }
  return out.filter((chunk) => chunk.text.trim() !== '');
}

const TAG_NAME = /^[A-Za-z_$][\w.$]*/;

// Walk an opening tag to its OWN `>`: a `>` inside a prop expression, a string or a
// comment is not the end of the tag, and a windowed regex cannot tell the difference.
function walkTag(src: string, start: number): { end: number; name: string } | null {
  const name = TAG_NAME.exec(src.slice(start + 1, start + 60))?.[0];
  if (name === undefined) return null;
  let i = start + 1 + name.length;
  let depth = 0;
  let quote = '';
  while (i < src.length) {
    const c = src[i] ?? '';
    if (quote !== '') {
      if (c === '\\') i += 1;
      else if (c === quote || (c === '\n' && quote !== '`')) quote = '';
    } else if (c === '/' && src[i + 1] === '/') {
      const nl = src.indexOf('\n', i);
      if (nl === -1) return null;
      i = nl;
    } else if (c === '/' && src[i + 1] === '*') {
      const close = src.indexOf('*/', i + 2);
      if (close === -1) return null;
      i = close + 1;
    } else if (c === '"' || c === "'" || c === '`') quote = c;
    else if (c === '{') depth += 1;
    else if (c === '}') depth -= 1;
    else if (c === '>' && depth === 0) return { end: i, name };
    i += 1;
  }
  return null;
}

/** Where the element's subtree ends: past `/>`, or past its own closing tag. */
function elementEnd(src: string, openEnd: number, name: string): number {
  if (src[openEnd - 1] === '/') return openEnd + 1;
  const close = `</${name}`;
  let depth = 1;
  let i = openEnd + 1;
  while (i < src.length) {
    if (src[i] === '<') {
      if (src.startsWith(close, i) && !/[\w.$]/.test(src[i + close.length] ?? '')) {
        depth -= 1;
        const gt = src.indexOf('>', i);
        if (depth === 0) return gt + 1;
        i = gt + 1;
        continue;
      }
      const inner = walkTag(src, i);
      if (inner && inner.name === name) {
        if (src[inner.end - 1] !== '/') depth += 1;
        i = inner.end + 1;
        continue;
      }
    }
    i += 1;
  }
  return src.length;
}

// `LayoutIcon` joined the list when AddStoryModal's collage tile stopped painting its
// selected fill as `bg-action-primary/5` and started saying `bg-info-tint`: the fill became
// measurable and the tile came up as `primary on primary-tint — 4.38:1`, held to the
// text floor. Its entire content is one `aria-hidden` `<svg>` whose cells are
// `fill-current`, which is a graphic under 1.4.11 and clears the 3:1 that asks of it.
// The list is components-whose-whole-render-is-an-svg, and it was short only because
// nothing measurable had ever sat behind this one.
//
// Один список на два вопроса — «что этот элемент СОДЕРЖИТ» и «чем этот элемент ЯВЛЯЕТСЯ», —
// и собраны из него оба: набор имён и регулярка. Двумя литералами они разошлись бы на
// первом же новом глифе, а расхождение тут тихое: элемент перестал бы считаться графикой
// и молча поехал бы к полу 4.5:1.
const GLYPH_TAGS = ['Icon', 'Spinner', 'LayoutIcon'] as const;
// `svg` отдельно: у него есть содержимое и закрывающий тег, а остальные три
// самозакрывающиеся.
const GLYPH = new RegExp(`<svg\\b[\\s\\S]*?</svg>|<(?:${GLYPH_TAGS.join('|')})\\b[^<>]*/>`, 'g');
const GLYPH_TAG = new Set<string>([...GLYPH_TAGS, 'svg']);

/** An element whose whole content is an icon is a graphic, not text. */
function glyphOnly(body: string): boolean {
  return body.includes('<') && body.replace(GLYPH, '').trim() === '';
}

/**
 * Открытых `{` между двумя точками исходника: строки и комментарии не считаются.
 *
 * Отвечает на «этот элемент применяется ВСЕГДА в теле того, кто его окружает». Ноль —
 * элемент написан в теле прямо; больше нуля — он внутри `{…}`, то есть внутри условия
 * (`{on && <Icon …/>}`) или внутри дерева, переданного пропом.
 */
function braceDepth(src: string, from: number, to: number): number {
  let depth = 0;
  let quote = '';
  for (let i = from; i < to; i += 1) {
    const c = src[i] ?? '';
    if (quote !== '') {
      if (c === '\\') i += 1;
      else if (c === quote || (c === '\n' && quote !== '`')) quote = '';
    } else if (c === '/' && src[i + 1] === '/') i = src.indexOf('\n', i);
    else if (c === '/' && src[i + 1] === '*') i = src.indexOf('*/', i) + 1;
    else if (c === '"' || c === "'" || c === '`') quote = c;
    else if (c === '{') depth += 1;
    else if (c === '}') depth -= 1;
    if (i < from) break;
  }
  return depth;
}

const BG = /(?:^|\s)(?:[\w-]+:)*bg-(\S+)/g;
// `stroke` и `fill` стоят рядом с `text`, и это правка: краска глифа — такое же решение о
// контрасте, как краска надписи, а до сих пор гейт её не видел вовсе. Попытка добавить их
// была и была откачена, потому что на десяти парах разбор ошибался в четырёх, и обе причины
// были структурными. Обе теперь закрыты:
//
//   • элемент, который САМ является глифом, не опознавался глифом. `glyphOnly` спрашивает,
//     что элемент СОДЕРЖИТ, — верно для обёртки и неверно для `<Icon className=
//     "stroke-on-success" />`, у которого тело пусто: его держали у пола текста 4.5:1
//     вместо 3:1, которые графике даёт 1.4.11. Теперь имя тега отвечает на тот же вопрос
//     напрямую — см. `GLYPH_TAG`.
//   • условный РЕБЁНОК не был ветвью. `{on && <Icon className="stroke-on-action" />}` для
//     обхода был всегда применённым куском, поэтому его белая краска сходилась с ОБЕИМИ
//     половинами родительского `on ? bg-action-primary : bg-surface-card` и давала белое на
//     белом, 1.00:1 — сочетание, которого не рисует ничто.
//
// Второе закрыто через причину, а не через симптом. Симптом — равные значения краски, и
// пропускать пару с равными hex было бы одной строкой; отказ измеренный: белым по белой
// карточке — это САМАЯ дорогая ошибка контраста из возможных (ровно та, что жила на кнопке
// проверки в таблице аккаунтов), и правило «равные значения не спрашиваем» перестало бы её
// ловить. Вместо этого условный ребёнок стал не-всегдашним куском по той же семантике, по
// которой ею уже были ветви шаблонного литерала: не-всегда не может встретиться с
// не-всегда. Цена названа — пара «белое на синем» в таком месте больше не измеряется
// обходом, и её держит рукописное утверждение в конце файла.
const INK = /(?:^|\s)(?:[\w-]+:)*(?:text|stroke|fill)-([a-z]+(?:-[a-z]+)?)(?![\w/-])/g;
const ROLE = /(?:^|\s)type-([a-z-]+)(?![\w-])/g;

const matches = (re: RegExp, text: string): string[] =>
  [...text.matchAll(re)].map((m) => m[1] ?? '');

/** Every background this class list can paint, measurable or not — an arbitrary or
 *  translucent fill still stops the fill above it from reaching the text. */
const fillsOf = (text: string): string[] => [...new Set(matches(BG, text))];

/** The ink this class list writes in: an explicit colour, else the one its role carries. */
function inksOf(text: string): string[] {
  const explicit = matches(INK, text).filter((token) => token in HEX);
  if (explicit.length > 0) return [...new Set(explicit)];
  const roles = matches(ROLE, text)
    .map((role) => ROLE_INK[role])
    .filter((ink): ink is string => ink !== undefined && ink in HEX);
  return [...new Set(roles)];
}

const sources = import.meta.glob('/src/**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

const stylesheets = import.meta.glob('/src/**/*.css', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

// A background can also be painted in CSS: `.tb-tip-pop` is the dark tooltip the log
// terminal's colours are read on. Those names are read out of the stylesheet rather
// than listed, and count only as "something is painted here" — the fill itself is
// beyond a class scan, so it ends the reach of the fill above it and is not measured.
const cssPainted = new Set<string>();
for (const sheet of Object.values(stylesheets)) {
  for (const block of sheet.matchAll(/\.(tb-[\w-]+)[^{}]*\{([^{}]*)\}/g)) {
    if (/background(?:-color)?\s*:\s*(?!transparent)/.test(block[2] ?? '')) {
      cssPainted.add(block[1] ?? '');
    }
  }
}
const CSS_FILL = new RegExp(`(?:^|\\s)(?:${[...cssPainted].join('|')})(?![\\w-])`);

const lineAt = (src: string, index: number) => src.slice(0, index).split('\n').length;

/**
 * Пары «краска на подложке», которые рисует ОДИН исходник.
 *
 * Функция, а не цикл верхнего уровня, и это половина правки. Пока разбор был циклом,
 * подсунуть ему подделку было нечем: проверялся результат разбора НА ПРИЛОЖЕНИИ, то есть
 * утверждение «сегодня чисто», а не утверждение «разбор смотрит». Гейт, который нельзя
 * проверить на фикстуре, отличается от сломанного гейта ровно тем, что в приложении
 * сегодня чисто.
 */
function scan(path: string, src: string): { offenders: string[]; pairings: string[] } {
  const offenders: string[] = [];
  const pairings: string[] = [];

  const record = (ink: string, fill: string, where: string, glyph: boolean) => {
    if (ink === fill || !(ink in HEX) || !(fill in HEX)) return;
    const key = `${ink} on ${fill}`;
    pairings.push(key);
    const floor = glyph ? NON_TEXT : AA;
    const measured = ratio(ink, fill);
    if (measured < floor) {
      offenders.push(`${where} ${key} — ${measured.toFixed(2)}:1, needs ${floor.toFixed(1)}`);
    }
  };

  // `open` — индекс своего `>`: с него считается, стоит ли ребёнок в теле прямо или
  // внутри `{…}`. `always` — те заливки элемента, которые применяются безусловно.
  const spans: {
    start: number;
    open: number;
    end: number;
    fills: string[];
    always: string[];
  }[] = [];
  const floating: { at: number; inks: string[]; glyph: boolean }[] = [];
  const inATag: [number, number][] = [];
  for (let i = src.indexOf('<'); i !== -1; i = src.indexOf('<', i + 1)) {
    const tag = walkTag(src, i);
    if (tag === null) continue;
    // Props can hold whole elements (`header={<span className=… />}`); their classes
    // belong to them, not to the tag they sit in, and they get their own turn here.
    const raw = src.slice(i, tag.end + 1);
    const nested = raw.slice(1).search(/<[A-Za-z]/);
    const own = nested === -1 ? raw : raw.slice(0, nested + 1);
    inATag.push([i, i + own.length]);
    const end = elementEnd(src, tag.end, tag.name);
    // Два вопроса, один ответ: элемент ЯВЛЯЕТСЯ глифом (`<Icon …/>`, `<svg>`) или
    // СОДЕРЖИТ один глиф и ничего больше (обёртка вокруг иконки). И то и другое —
    // графика под 1.4.11, и пол у неё 3:1.
    const glyph =
      GLYPH_TAG.has(tag.name) ||
      glyphOnly(src.slice(tag.end + 1, Math.max(tag.end + 1, end - tag.name.length - 3)));
    const cs = chunks(own);
    const fills = [...new Set(cs.flatMap((c) => fillsOf(c.text)))];
    if (cs.some((c) => CSS_FILL.test(c.text))) fills.push('css');
    const always = [...new Set(cs.filter((c) => c.always).flatMap((c) => fillsOf(c.text)))];
    if (fills.length > 0) spans.push({ start: i, open: tag.end, end, fills, always });
    const orphaned: string[] = [];
    for (const chunk of cs) {
      const inks = inksOf(chunk.text);
      if (inks.length === 0) continue;
      const own_ = fillsOf(chunk.text);
      // A fill and an ink meet when they share a chunk, or when either of them always
      // applies. Two different conditional branches never meet.
      const reach = new Set([...own_, ...(chunk.always ? fills : always)]);
      const where = `${path}:${lineAt(src, i + chunk.at)}`;
      for (const fill of reach) for (const ink of inks) record(ink, fill, where, glyph);
      if (fills.length === 0) orphaned.push(...inks);
    }
    if (orphaned.length > 0) floating.push({ at: i, inks: orphaned, glyph });
  }
  // Text with no fill of its own is read on the nearest painted ancestor.
  for (const { at, inks, glyph } of floating) {
    const enclosing = spans.filter((s) => s.start < at && at < s.end);
    if (enclosing.length === 0) continue;
    const nearest = enclosing.reduce((a, b) => (b.start > a.start ? b : a));
    // Та же семантика «всегда», что у кусков шаблонного литерала, только осью выше:
    // ребёнок внутри `{…}` в теле предка применяется НЕ всегда, поэтому встретиться он
    // может только с заливкой, которая применяется всегда. Иначе `{on && <Icon
    // className="stroke-on-action"/>}` сходится с обеими половинами родительского
    // тернарника и приносит белое на белом — пару, которой не рисует ничто.
    const reach = braceDepth(src, nearest.open + 1, at) === 0 ? nearest.fills : nearest.always;
    const where = `${path}:${lineAt(src, at)}`;
    for (const fill of reach) for (const ink of inks) record(ink, fill, where, glyph);
  }
  // The class lists that never reach a tag: the tone maps and hoisted constants, which
  // is where thirty-five status pills sat on the failing rung after the first sweep.
  for (const chunk of chunks(src)) {
    if (inATag.some(([a, b]) => a <= chunk.at && chunk.at < b)) continue;
    const inks = inksOf(chunk.text);
    const where = `${path}:${lineAt(src, chunk.at)}`;
    for (const fill of fillsOf(chunk.text)) for (const ink of inks) record(ink, fill, where, false);
  }
  return { offenders, pairings };
}

const scanned = Object.entries(sources)
  .filter(([path]) => !path.includes('.test.'))
  .map(([path, src]) => scan(path, src));
const offenders = scanned.flatMap((one) => one.offenders);
const pairings = new Set(scanned.flatMap((one) => one.pairings));

// A source-reading assertion can lie in exactly one way: by reading nothing. A glob that
// resolved to nothing, or a walker that stopped finding tags, would leave both of these
// empty and every assertion below would pass on it.
test('the scan reads the tree it claims to', () => {
  expect(Object.keys(sources).length).toBeGreaterThan(100);
  expect(cssPainted.size).toBeGreaterThan(0);
  expect(pairings.size).toBeGreaterThan(40);
  // Две пары-часовых, и вторая из них — то, чего до переезда на роли измерить было
  // нельзя: `white on primary` описывало и надпись на кнопке, и белую карточку одним
  // именем. Теперь «чернила НА залитом действии» — своя пара, и она под своим полом.
  expect([...pairings]).toContain('content-subtle on surface-card');
  expect([...pairings]).toContain('on-action on action-primary');
  // И третья: краска ГЛИФА, которой этот обход до правки не видел вовсе.
  expect([...pairings]).toContain('on-success on success');
});

test('every text-on-fill pairing the source paints clears its floor', () => {
  // Joined rather than compared as an array: a failure has to name the file, the line
  // and the measurement, and a diff of two arrays truncates at two entries.
  expect(offenders.join('\n')).toBe('');
});

// ── Разбор на подделках ─────────────────────────────────────────────────────────────
//
// Смысл отрицательного случая тут один: он ломается, когда обход ПЕРЕСТАЁТ смотреть.
// Утверждение «в приложении чисто» пустой обход выполняет молча, поэтому каждая фикстура
// ниже названа своей причиной, а не «случаем 1».
const FIXTURES: { name: string; source: string; offends: boolean; because: string }[] = [
  {
    name: 'краска на самом глифе внутри тонированной подложки',
    source: `<span className="bg-success-tint">
      <Icon name="check" size={16} className="stroke-success" />
    </span>`,
    offends: true,
    because: 'базовый зелёный на своём тоне — 2.97:1 против 3:1, которые просит графика',
  },
  {
    name: 'то же на голом svg',
    source: `<span className="bg-success-tint">
      <svg className="fill-success" />
    </span>`,
    offends: true,
    because: '`fill-` читается наравне с `stroke-`, и `svg` — такой же глиф',
  },
  {
    name: 'СЛОВО, а не глиф, на залитом тоне',
    source: `<span className="bg-success"><span className="text-on-success">жив</span></span>`,
    offends: true,
    because: 'белый на базовом зелёном — 3.37:1, а слово держат у 4.5:1',
  },
  {
    name: 'глиф, который берёт 3:1 и не берёт 4.5:1',
    source: `<span className="bg-success">
      <Icon name="check" size={10} className="stroke-on-success" />
    </span>`,
    offends: false,
    because: 'те же 3.37:1, но это графика: пол 3:1, и она его берёт',
  },
  {
    name: 'условный ребёнок под условной заливкой',
    source: `<span className={\`flex \${on ? 'bg-action-primary' : 'bg-surface-card'}\`}>
      {on && <Icon name="check" size={14} className="stroke-on-action" />}
    </span>`,
    offends: false,
    because: 'не-всегда не встречается с не-всегда: белое на белом тут не рисуется',
  },
];

test('разбор ловит краску глифа и не выдумывает того, чего не рисуют', () => {
  const verdicts = FIXTURES.map((fixture) => {
    const found = scan('fixture.tsx', fixture.source);
    return `${fixture.name}: ${found.offenders.length > 0 ? 'нарушение' : 'чисто'}`;
  });
  expect(verdicts).toEqual(FIXTURES.map((f) => `${f.name}: ${f.offends ? 'нарушение' : 'чисто'}`));
  // Измерение, а не только вердикт: «нарушение» на верном поле и на неверном выглядит
  // одинаково, а полов тут два, и вся правка про то, какой из них берётся.
  expect(scan('fixture.tsx', FIXTURES[0]?.source ?? '').offenders.join()).toContain(
    '2.97:1, needs 3.0',
  );
  expect(scan('fixture.tsx', FIXTURES[2]?.source ?? '').offenders.join()).toContain(
    '3.37:1, needs 4.5',
  );
});

// The one kind of pairing the scan above cannot see: a fill painted in one component
// and the ink read on it written in another. `LogRow` spells the account column
// `text-term-text`; the surface under it is `bg-term`, painted by `LogTerminal` further
// down the same file. Nothing in either class list names the other, so this stays a
// hand-written line — and it is the only one left, where the table used to be
// twenty-four with no way to tell which of them were still real.
test('term.text reads on the terminal surface it is written for', () => {
  expect(ratio('term-text', 'term')).toBeGreaterThanOrEqual(AA);
});

// The other shape the scan cannot see: ink painted with `stroke`/`fill`, and ink chosen
// by an index (`roleTone(i).on`) rather than written as a class. Both are how the three
// `on-*` roles are worn, so each is measured here against the fill it is actually worn
// on — read off the sites, not off the tone's name.
//
// `on-success on success` is 3.37:1: the graphic floor, and it is a graphic — the five
// wearers are all a check inside a filled circle. It does NOT clear the text floor, and
// asserting that is the point: putting a WORD on `bg-success` is the mistake this line
// exists to catch, and the tone already has `deep` for it.
test('ink on a filled tone reads on the fill it is worn on', () => {
  expect(ratio('on-success', 'success')).toBeGreaterThanOrEqual(NON_TEXT);
  expect(ratio('on-success', 'success')).toBeLessThan(AA);
  expect(ratio('on-success', 'success-deep')).toBeGreaterThanOrEqual(AA);
  expect(ratio('on-danger', 'danger')).toBeGreaterThanOrEqual(AA);
  // Янтарный носится ТОЛЬКО на `deep`, и это не случайность: на базовом янтаре белый
  // мерит 4.01:1, то есть под полом. Второе утверждение держит первое честным — оно
  // ломается в тот день, когда кто-нибудь наденет `on-warning` на `bg-warning`.
  expect(ratio('on-warning', 'warning-deep')).toBeGreaterThanOrEqual(AA);
  expect(ratio('on-warning', 'warning')).toBeLessThan(AA);
});
