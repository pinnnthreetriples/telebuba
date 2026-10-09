import postcss from 'postcss';
import { describe, expect, test } from 'vitest';

import { flatColors, fontWeight, typeRole, typeScale } from '@/shared/design-system/tokens';

import { compileClasses, ruleBody } from './tailwind.test-helpers';

// Стили объявлены в токенах и выходят в CSS через сгенерированную тему, поэтому проверять
// стоит CSS, который Tailwind действительно выпускает, а не объект, из которого он
// выпущен. Каждое ожидание выведено из собственных ступеней, весов и краски токенов:
// перенастроенная ступень остаётся верной здесь без правки теста, а стиль, переставший
// разрешать свой токен, падает громко вместо того, чтобы ничего не покрасить.
const steps: Record<string, { size: string; leading: string; tracking: string }> = typeScale;
const roles: Record<string, { size: string; weight: string; ink: string }> = typeRole;
const weights: Record<string, string> = fontWeight;

function inkHex(token: string): string {
  const content: Record<string, string> = flatColors.content;
  const hex = content[token.slice('content-'.length)];
  if (hex === undefined) throw new Error(`нет краски ${token}`);
  return hex;
}

const fixture = [
  ...Object.keys(roles).map((name) => `type-${name}`),
  ...Object.keys(steps).map((name) => `text-${name}`),
  // Нарочно: тест доказывает, что отставные веса НЕ выпускают правил.
  // eslint-disable-next-line design-tokens/no-raw-values
  ...'font-semibold font-bold'.split(' '),
];

const css = await compileClasses(fixture);

/** Слой, в котором стоит правило `selector`, — или `null`, если правило без слоя. */
function layerOf(selector: string): string | null {
  let found: string | null | undefined;
  postcss.parse(css).walkRules((rule) => {
    if (found !== undefined || !rule.selectors.includes(selector)) return;
    found = null;
    for (let parent = rule.parent; parent && parent.type !== 'root'; parent = parent.parent) {
      if (parent.type === 'atrule' && (parent as postcss.AtRule).name === 'layer') {
        found = (parent as postcss.AtRule).params;
        break;
      }
    }
  });
  if (found === undefined) throw new Error(`Tailwind не выпустил ${selector}`);
  return found;
}

describe('каждый стиль становится классом, который красит его целиком', () => {
  for (const [name, role] of Object.entries(roles)) {
    test(name, () => {
      const body = ruleBody(css, `.type-${name}`);
      const step = steps[role.size];
      if (step === undefined) throw new Error(`стиль ${name}: нет ступени ${role.size}`);
      expect(body).toContain(`font-size: ${step.size}`);
      expect(body).toContain(`line-height: ${step.leading}`);
      expect(body).toContain(`letter-spacing: ${step.tracking}`);
      expect(body).toContain(`font-weight: ${String(weights[role.weight])}`);
      expect(body).toContain(`color: ${inkHex(role.ink)}`);
    });
  }
});

// Ступень — размер ВМЕСТЕ со своим интерлиньяжем и трекингом: `text-small` на значке
// или в строке журнала не наследует 20px от тела.
describe('каждая ступень ставит размер, интерлиньяж и трекинг одним классом', () => {
  for (const [name, step] of Object.entries(steps)) {
    test(name, () => {
      const body = ruleBody(css, `.text-${name}`);
      expect(body).toContain(`font-size: ${step.size}`);
      // `var(--tw-leading, …)`: Tailwind 4 даёт `leading-*` на том же элементе перекрыть
      // ступень. Своё значение ступень несёт запасным.
      expect(body).toContain(`line-height: var(--tw-leading, ${step.leading})`);
      expect(body).toContain(`letter-spacing: var(--tw-tracking, ${step.tracking})`);
    });
  }
});

test('интерлиньяж каждой ступени кратен 4px, а размеры идут строго по возрастанию', () => {
  const sizes = Object.values(steps).map((step) => Number.parseFloat(step.size));
  expect([...sizes].sort((a, b) => a - b)).toEqual(sizes);
  for (const step of Object.values(steps)) {
    expect(Number.parseFloat(step.leading) % 4).toBe(0);
  }
});

// Шкала весов заменена, а не расширена: `font-semibold` и `font-bold` не должны выпускать
// ничего, иначе жирность снова начнёт делать иерархию мимо ступеней.
test('весов два, и тяжёлые имена Tailwind не выпускают правил', () => {
  expect(Object.values(weights)).toEqual(['400', '500']);
  expect(css).not.toContain('.font-semibold');
  expect(css).not.toContain('.font-bold');
});

// Стиль должен проигрывать утилите на том же элементе — это то, что делает
// `type-small text-danger` строкой ошибки. Слой `components` объявлен раньше `utilities`
// (`index.css`), поэтому доказательство — то, в каком слое стоит правило.
test('стили выпускаются в слой components, а ступени — в utilities', () => {
  expect(layerOf('.type-small')).toBe('components');
  expect(layerOf('.text-small')).toBe('utilities');
  expect(css).toMatch(/@layer theme, base, components, utilities;/);
});
