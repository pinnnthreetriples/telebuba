import postcss, { type AtRule, type Rule } from 'postcss';
import tailwind from 'tailwindcss';
import { describe, expect, test } from 'vitest';

import config from '../../../tailwind.config';

import source from './index.css?raw';

// index.css holds the parts Tailwind utilities cannot express, and two of them are
// invisible to every other gate this repo has: an animation that keeps moving under
// `prefers-reduced-motion` is not a raw value, not a contrast failure, not a drift
// between the config and the document, and not a type error — and a tooltip that only
// opens on `:hover` is a rule that is CORRECT for every pointer and missing for every
// keyboard. Neither can be reached from a component test, because happy-dom applies no
// stylesheet at all: the components can only be asked whether their side of the contract
// is there (a focusable trigger, an `aria-describedby` that resolves), and this file asks
// the stylesheet for the other side.
//
// It runs the real PostCSS/Tailwind pipeline over the file rather than reading its text,
// so a `theme()` call that stopped resolving fails here too. `?raw` rather than a path off
// `import.meta.url`, which under Vite is not a `file:` URL. The `@import`s at the top are
// left alone — nothing in this file resolves them, and nothing here asks about fonts.
const { root } = await postcss([
  tailwind({ ...config, content: [{ raw: '', extension: 'html' }] }),
]).process(source, {
  from: undefined,
});

function reducedMotionBlocks(): AtRule[] {
  const found: AtRule[] = [];
  root.walkAtRules('media', (at) => {
    if (at.params.includes('prefers-reduced-motion')) found.push(at);
  });
  return found;
}

describe('prefers-reduced-motion', () => {
  // One blanket block, not a list of class names. The list is what this used to be, and
  // it reached three of the app's twenty-odd moving things — it could never have reached
  // the eight `[animation:…]` arbitrary utilities written inline in five components,
  // because those classes were minted by Tailwind and named nowhere in this file. Those
  // eight are named classes now, so the blanket could in principle go back to being a
  // list; it does not, because a moving thing should stop by default rather than by
  // someone remembering to add it here.
  test('is one blanket block covering every element and pseudo-element', () => {
    const blocks = reducedMotionBlocks();
    expect(blocks).toHaveLength(1);

    const selectors = new Set<string>();
    blocks[0]?.walkRules((rule: Rule) => {
      for (const selector of rule.selectors) selectors.add(selector);
    });
    expect(selectors).toEqual(new Set(['*', '*::before', '*::after']));
  });

  // The whole reason this is a near-zero DURATION and not `animation: none` /
  // `transition: none`: two of the app's transitions carry logic on their completion
  // event, and a removed transition fires no event at all. CollapsibleCard keys
  // `.tb-settled` off the open's max-height transitionend and `hidden` off the close's
  // opacity one; DataTable unmounts a closing sub-row on its grid-template-rows
  // transitionend. Kill those and a collapsed card keeps its entire body in the tab
  // order and the a11y tree — which is a WORSE accessibility bug than the motion this
  // block exists to stop.
  test('stops the motion without stopping the completion events', () => {
    const declared = new Map<string, string>();
    reducedMotionBlocks()[0]?.walkDecls((decl) => {
      declared.set(decl.prop, decl.value);
      expect(decl.important).toBe(true);
    });

    expect(declared.get('animation-duration')).toBe('0.01ms');
    expect(declared.get('transition-duration')).toBe('0.01ms');
    // A near-zero duration repeated forever is still a flicker, so the infinite pulses
    // need the iteration count too.
    expect(declared.get('animation-iteration-count')).toBe('1');
    expect(declared.get('animation-delay')).toBe('0ms');
    expect(declared.get('transition-delay')).toBe('0ms');

    // The shorthands are the trap: `animation: none` and `transition: none` would satisfy
    // "the motion stopped" and take the two transitionend handlers with them.
    expect(declared.has('animation')).toBe(false);
    expect(declared.has('transition')).toBe(false);
  });

  // `!important` on `*` is what beats a `transition:`/`animation:` SHORTHAND declared
  // later in the file — a media query adds no specificity, and a shorthand resets every
  // longhand a plain override set. `.tb-subrow` used to carry its own exemption BELOW its
  // base rule for exactly that reason; this is what replaced it.
  test('the blanket outranks every animation and transition shorthand in the file', () => {
    const shorthands: string[] = [];
    root.walkDecls((decl) => {
      if (decl.prop === 'animation' || decl.prop === 'transition') {
        // PostCSS leaves `important` undefined rather than false on a plain declaration.
        expect(decl.important ?? false).toBe(false);
        shorthands.push(decl.prop);
      }
    });
    // If this ever reads zero the assertion above stopped meaning anything.
    expect(shorthands.length).toBeGreaterThan(10);
  });
});

// The config says the app has two easing curves and that a third one this file carried
// "is gone". That was true of the `transition:` declarations, which were tokenised, and
// false of the `animation:` shorthands, which the same sweep never touched and which
// kept two more curves for months: `.tb-blur` at `(.34,1.56,.64,1)` beside `spring`, and
// `.tb-drawerin` at `(.22,1,.36,1)` beside `out`. A config that documents a value as
// gone while the stylesheet still paints it is worse than one that never claimed it, so
// the claim gets a gate rather than a comment.
//
// Asserted against the SOURCE text and not the compiled `root` above: every `theme()`
// call resolves to a literal `cubic-bezier(...)` on the way through, so the compiled
// sheet is full of them by design. The compiled side is still what proves the names
// resolve — an unknown `transitionTimingFunction` key throws in the PostCSS run at the
// top of this file, before any test here gets to make an assertion.
test('the stylesheet spends the config’s curves and never writes one out', () => {
  const literals = [...source.matchAll(/cubic-bezier\([^)]*\)/g)].map((hit) => hit[0]);
  expect(literals).toEqual([]);
  // The other half of the claim: the two that were strays still ease. A rule with no
  // easing at all would satisfy the assertion above and silently drop both to `ease`.
  for (const selector of ['.tb-blur', '.tb-drawerin']) {
    const rule = source.slice(source.indexOf(`${selector} {`));
    expect(rule.slice(0, rule.indexOf('}'))).toMatch(/theme\('transitionTimingFunction\.\w+'\)/);
  }
});

// Тот же довод, что у кривых, одной осью в сторону — и он держался хуже: восемь
// объявлений `box-shadow` внутри кейфреймов носили краску литералом. Это была вторая
// запись значения, которое палитра уже хранит, поэтому, перекрасив `blue600`, кольцо
// пульса осталось бы прежним.
//
// Проверка под это переписана дважды, и обе прошлые версии ошибались ОДИНАКОВО — брали
// открытый список за закрытый:
//
//   1. Первая банила ФОРМАТЫ (hex, `rgb(` с числом в первом аргументе) и потому
//      пропускала `transparent`, `red`, `oklch()`, `color()`, `currentColor`, `Canvas`.
//   2. Вторая перечисляла СВОЙСТВА, способные нести краску, — и её провели насквозь
//      через `background-image: linear-gradient(red, blue)` и `filter: drop-shadow(red
//      0 0)`: ни того, ни другого в списке не было. Заодно любой `theme()` считался
//      краской, поэтому `color: theme('spacing.md')` проходил как токен.
//
// Закрытый список тут ровно один, и он на другой стороне: перечислить КРАСКИ можно —
// имена CSS Color 4 не пополнялись с 2014 года (`rebeccapurple`), — а перечислить
// свойства, которые их несут, нельзя. Поэтому свойства больше не перечисляются вовсе:
// сканируется значение КАЖДОГО объявления, и вопрос к атому не «законен ли он здесь», а
// «краска ли это».
//
// Три правила:
//
//   (A) ни в одном объявлении нет атома, который является краской: hex, имя функции
//       цвета, имя из CSS Color 4, системное имя, `transparent`, `currentColor`.
//       Свойство не спрашивается — отсюда и закрылись `background-image` и `filter`.
//   (B) свойство, которое несёт ТОЛЬКО краску (`color`, `*-color`, `fill`, `stroke`),
//       не содержит ничего, кроме вызовов `theme('colors.…')`. Отсюда закрылся
//       `color: theme('spacing.md')`: непалитровый токен оставляет остаток.
//   (C) hex не встречается в файле вообще, включая комментарии: hex в комментарии — это
//       вторая запись значения, ровно тот дефект, с которого гейт начался.
//
// Разбор — plain PostCSS по ИСХОДНИКУ, а не скомпилированный `root` из шапки файла: там
// каждый `theme()` уже развёрнут в литерал, и отличить токен от краски нечем. Что путь
// внутри `theme()` существует, доказывает не гейт, а компиляция в шапке: она падает на
// неизвестном ключе.
//
// Чего гейт не делает: на составных свойствах (`background`, `border`, `box-shadow`) он
// не спрашивает, из какой шкалы токен. Там длина законна по позиции — `box-shadow` берёт
// из `spacing` смещение, — и правило «только палитра» дало бы ложное срабатывание. Токен
// не той шкалы даёт там невалидный CSS, который не красит ничего; молча покрасить не тем
// цветом он не может.
//
// `@apply` отдельной проверки не требует: класс несуществующего цвета до гейта не
// доживает — он падает при компиляции в шапке этого файла, потому что палитра Tailwind
// ЗАМЕНЕНА, а не расширена.

// Имена CSS Color 4, системные имена и два ключевых слова.
const NAMED = new Set(
  `aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue
   blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk
   crimson cyan darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki
   darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen
   darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue
   dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro
   ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink indianred indigo
   ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan
   lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen
   lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime limegreen
   linen magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple
   mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred
   midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab
   orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip
   peachpuff peru pink plum powderblue purple rebeccapurple red rosybrown royalblue
   saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue slateblue
   slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet
   wheat white whitesmoke yellow yellowgreen
   transparent currentcolor
   canvas canvastext linktext visitedtext activetext buttonface buttontext buttonborder
   field fieldtext highlight highlighttext selecteditem selecteditemtext mark marktext
   graytext accentcolor accentcolortext`.split(/\s+/),
);

const HEX = /#[0-9a-fA-F]{3,8}/;
const COLOUR_FN = /^(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix|light-dark)$/;
// Атомы значения: разделители CSS — пробел, запятая, скобки и слэш.
const ATOM = /[^\s,()/]+/g;
// Вызов из палитры: единственное, что законно на свойстве только-краски.
const PALETTE_CALL = /theme\((['"])colors\.[\w.-]+\1\)/g;

function isColour(atom: string): boolean {
  const word = atom.toLowerCase();
  return HEX.test(atom) || COLOUR_FN.test(word) || NAMED.has(word);
}

// Законные формы стягиваются в один непрозрачный жетон, ПРЕЖДЕ чем что-либо искать.
// Порядок важен: альфа поверх канала — тоже `rgb(`, и без первого шага правило (A)
// поймало бы её за имя функции.
function withoutTokens(text: string): string {
  return text
    .replace(/rgba?\(\s*theme\((['"])channel\.[\w.-]+\1\)\s*\/\s*[\d.]+\s*\)/g, 'TOKEN')
    .replace(/theme\((['"])[\w.-]+\1\)/g, 'TOKEN');
}

// Свойство, которое не несёт ничего, кроме краски.
function paintOnly(prop: string): boolean {
  return /(?:^|-)colou?r$/.test(prop) || prop === 'fill' || prop === 'stroke';
}

// Гейт — функция от текста, а не от импортированного `source`, ради отрицательных
// случаев ниже: гейт, у которого их нет в CI, — это гейт, про который неизвестно,
// смотрит ли он ещё.
function paintOffenders(css: string): string[] {
  const offenders: string[] = [];

  postcss.parse(css).walkDecls((decl) => {
    const at = `${String(decl.source?.start?.line)}: ${decl.prop}: ${decl.value}`;

    const literals = (withoutTokens(decl.value).match(ATOM) ?? []).filter(isColour);
    if (literals.length > 0) {
      offenders.push(`${at} — краска литералом: ${literals.join(' ')}`);
    }

    if (!paintOnly(decl.prop)) return;
    const rest = decl.value.replace(PALETTE_CALL, '').trim();
    if (rest !== '') offenders.push(`${at} — на краске не палитра: ${rest}`);
  });

  return offenders;
}

test('всё, чем этот файл красит, приходит жетоном из палитры', () => {
  expect(paintOffenders(source)).toEqual([]);

  // Вторая половина утверждения: кадры, которые красились, красятся до сих пор. Правило
  // вообще без краски удовлетворило бы всё выше и молча погасило бы кольцо.
  for (const name of ['plpulse', 'livepulse', 'loadpulse']) {
    const frame = source.slice(source.indexOf(`@keyframes ${name} {`));
    const body = frame.slice(0, frame.search(/^\}/m));
    expect(body).toMatch(/rgb\(theme\('channel\.\w+'\)/);
  }
});

// Первые три — обходы, которые нашло ревью у предыдущей версии. Остальные держат закрытым
// то, что она банила перечислением форматов.
test.each([
  ['непалитровый токен на краске', ".x { color: theme('spacing.md'); }"],
  ['ключевое слово в градиенте', '.x { background-image: linear-gradient(red, blue); }'],
  ['ключевое слово в фильтре', '.x { filter: drop-shadow(red 0 0); }'],
  ['ключевое слово в упор', '.x { background: transparent; }'],
  ['системное имя', '.x { background: Canvas; }'],
  ['унаследованная краска', '.x { border-bottom-color: currentColor; }'],
  ['функция, которой раньше не было', '.x { color: oklch(0.7 0.1 250); }'],
  ['функция со смешиванием', '.x { background: color-mix(in srgb, white 50%, black); }'],
  // Ниже — сам дефект: ровно то, что стояло в кейфрейме. Записать его иначе значит
  // перестать им быть, поэтому правило здесь снято адресно.
  // eslint-disable-next-line design-tokens/no-raw-values -- это фикстура, а не краска
  ['исходный дефект', '.x { box-shadow: 0 0 0 0 rgba(0, 102, 255, 0.3); }'],
  [
    'hex в свойстве, которого нет ни в одном списке',
    '.x { mask-image: linear-gradient(#000, #fff); }',
  ],
])('гейт ловит обход: %s', (_case, css) => {
  expect(paintOffenders(css)).not.toEqual([]);
});

// (C) Комментарии `walkDecls` не видит, а hex в комментарии — это вторая запись значения,
// ровно тот дефект, с которого гейт начался. Отдельная построчная проверка по всему
// тексту; она же называет номер строки.
test('hex не встречается в файле вообще, включая комментарии', () => {
  const offenders = source
    .split(/\r?\n/)
    .map((line, at) => ({ line: line.trim(), at: at + 1 }))
    .filter(({ line }) => HEX.test(line))
    .map(({ line, at }) => `${String(at)}: ${line}`);

  expect(offenders).toEqual([]);
});

// The dark tooltip and the light one (`HelpHint`'s `HintBubble`) are two deliberate
// LOOKS — dark for a control's label, light for an explanation, per the canon. A look is
// not a reason for one of them to be pointer-only, and the light one has carried
// `group-focus-within:block` since it was written.
test('the dark tooltip opens on focus as well as hover', () => {
  const selectors: string[] = [];
  root.walkRules((rule: Rule) => {
    if (rule.selectors.some((selector) => selector.endsWith('.tb-tip-pop'))) {
      selectors.push(...rule.selectors);
    }
  });

  expect(selectors).toContain('.tb-tip:hover .tb-tip-pop');
  expect(selectors).toContain('.tb-tip:focus-within .tb-tip-pop');
});
