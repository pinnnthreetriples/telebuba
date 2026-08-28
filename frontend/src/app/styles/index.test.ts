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
// объявлений `box-shadow` внутри кейфреймов носили `rgba(0, 102, 255, 0.3)` литералом.
// Это была вторая запись значения, которое палитра уже хранит, поэтому, перекрасив
// `blue600`, кольцо пульса осталось бы прежним. Нашло ревью, а не этот файл, и причина
// поучительна: прежняя проверка искала `cubic-bezier` — то есть ровно то, что однажды
// сломалось, — а не КЛАСС «литерал вместо токена».
//
// Первая версия этой проверки повторила ту же ошибку на шаг выше: она искала ФОРМАТЫ
// (`#hex`, `rgb(`, `hsl(`) и потому пропускала `transparent`, `red`, `oklch()`,
// `color()`, `currentColor` и любое системное имя вроде `Canvas`. Перечислить все
// форматы краски нельзя — их набор в CSS растёт. Поэтому проверок теперь две, и вторая
// лишь сеть под первой.
//
// ── (1) Белый список: всё, чем красят, — жетон ──────────────────────────────────────
//
// Для КАЖДОГО объявления на свойстве, способном нести краску, законные формы стягиваются
// в один непрозрачный жетон, и в остатке не должно остаться ни одного слова, кроме
// структурных (`solid`, `none`, …). Утверждение закрытое: неизвестное слово — нарушение
// по умолчанию, а значит правило ловит и `transparent`, и `red`, и функцию цвета,
// которой в CSS ещё нет. Цена — ложное срабатывание на новом СТРУКТУРНОМ слове (`dashed`
// рядом с `solid`); лечится одним словом в списке, и это дешевле пропущенной краски.
//
// Разбор — plain PostCSS по ИСХОДНИКУ, а не скомпилированный `root` из шапки файла: там
// каждый `theme()` уже развёрнут в литерал, и отличить токен от краски нечем.
//
// ── (2) Чёрный список: сеть под остальными свойствами ───────────────────────────────
//
// Краска пролезает и туда, где свойство краской не считается: `filter: drop-shadow(#000
// …)`, `mask`, `background-image`. Перечислить такие свойства тоже нельзя, поэтому по
// всему тексту, построчно, ищутся hex и функциональные записи — уже ПОСЛЕ стягивания
// законных форм, так что различение «первый аргумент — число» больше не нужно: любой
// оставшийся `rgb(` незаконен сам по себе.
//
// Скан построчный, через `RegExp.test`, а не `matchAll` по всему тексту. Причина
// эмпирическая и неприятная: в этом окружении `matchAll` дважды вернул пустой список на
// строке, где совпадение заведомо есть (здесь и в `e2e/pages.spec.ts`), при том что
// `test` на той же строке и та же регулярка в изолированном тесте работают. Причину я не
// нашёл, и это ровно повод не оставлять его в гейте: проверка, чьё поведение непонятно,
// зеленеет молча. Построчный `test` — самый простой примитив, какой тут возможен, и он
// заодно называет номер строки.
//
// `@apply` отдельной проверки не требует: класс несуществующего цвета до гейта не
// доживает — он падает при компиляции в шапке этого файла, потому что палитра Tailwind
// ЗАМЕНЕНА, а не расширена.
function withoutTokens(text: string): string {
  return (
    text
      // Альфа поверх канала — законная форма, и в ней тоже есть `rgb(`. Стягивается
      // первой, иначе её съел бы чёрный список.
      .replace(/rgba?\(\s*theme\((['"])[^'"]+\1\)\s*\/\s*[\d.]+\s*\)/g, 'TOKEN')
      .replace(/theme\((['"])[^'"]+\1\)/g, 'TOKEN')
  );
}

// Свойство, способное нести краску: любое со словом `color`, набор шорткатов и
// пользовательские свойства — значение переменной видно только в месте применения, а
// туда этот разбор не дойдёт.
function paints(prop: string): boolean {
  return (
    prop.startsWith('--') ||
    /colou?r/.test(prop) ||
    /^(?:background|border(?:-(?:top|right|bottom|left))?|outline|box-shadow|text-shadow|fill|stroke)$/.test(
      prop,
    )
  );
}

// Значение разбирается на атомы по разделителям CSS, и законны ровно три вида: число с
// единицей, структурное слово и сам жетон. Всё прочее — краска: и `transparent`, и
// `#fff`, и имя функции, которой в CSS ещё нет.
const ATOM = /[^\s,()/]+/g;
const LENGTH = /^-?[\d.]+[a-z%]*$/i;
const STRUCTURAL = new Set(['TOKEN', 'solid', 'dashed', 'dotted', 'none', 'inset']);

const COLOUR_NOTATION =
  /#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix|light-dark|device-cmyk)\(/;

test('всё, чем этот файл красит, приходит жетоном', () => {
  const offenders: string[] = [];
  let examined = 0;

  postcss.parse(source).walkDecls((decl) => {
    if (!paints(decl.prop)) return;
    examined += 1;
    const stray = (withoutTokens(decl.value).match(ATOM) ?? []).filter(
      (atom) => !LENGTH.test(atom) && !STRUCTURAL.has(atom),
    );
    if (stray.length > 0) {
      offenders.push(`${String(decl.source?.start?.line)}: ${decl.prop}: ${decl.value}`);
    }
  });

  expect(offenders).toEqual([]);
  // Если это когда-нибудь прочтётся нулём, утверждение выше перестало что-либо значить.
  expect(examined).toBeGreaterThan(15);
});

test('и ни одной краски литералом — ни в одном формате, ни на одном свойстве', () => {
  const offenders = source
    .split(/\r?\n/)
    .map((line, at) => ({ line: line.trim(), at: at + 1 }))
    .filter(({ line }) => COLOUR_NOTATION.test(withoutTokens(line)))
    .map(({ line, at }) => `${String(at)}: ${line}`);

  expect(offenders).toEqual([]);

  // Вторая половина утверждения: кадры, которые красились, красятся до сих пор. Правило
  // вообще без краски удовлетворило бы обе проверки выше и молча погасило бы кольцо.
  for (const name of ['plpulse', 'livepulse', 'loadpulse']) {
    const frame = source.slice(source.indexOf(`@keyframes ${name} {`));
    const body = frame.slice(0, frame.search(/^\}/m));
    expect(body).toMatch(/rgb\(theme\('channel\.\w+'\)/);
  }
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
