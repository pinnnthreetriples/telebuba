import postcss from 'postcss';
import tailwind from 'tailwindcss';
import { describe, expect, test } from 'vitest';

import config from '../../../tailwind.config';

// Стили объявлены в токенах и рисуются плагином, поэтому проверять стоит CSS, который
// Tailwind действительно выпускает, а не объект, из которого он выпущен. Каждое ожидание
// выведено из собственных ступеней, весов и краски конфига: перенастроенная ступень
// остаётся верной здесь без правки теста, а стиль, переставший разрешать свой токен,
// падает громко вместо того, чтобы ничего не покрасить.
type Step = { size: string; leading: string; tracking: string };
type Role = { size: string; weight: string; ink: string };

const theme = config.theme as unknown as {
  typeScale: Record<string, Step>;
  typeRole: Record<string, Role>;
  fontWeight: Record<string, string>;
  colors: { content: Record<string, string> };
};
const { typeScale: steps, typeRole: roles, fontWeight: weights } = theme;

function inkHex(token: string): string {
  const hex = theme.colors.content[token.slice('content-'.length)];
  if (hex === undefined) throw new Error(`нет краски ${token}`);
  return hex;
}

const fixture = [
  ...Object.keys(roles).map((name) => `type-${name}`),
  ...Object.keys(steps).map((name) => `text-${name}`),
  // Нарочно: тест доказывает, что отставные веса НЕ выпускают правил.
  // eslint-disable-next-line design-tokens/no-raw-values
  'font-semibold font-bold',
].join(' ');

async function compile(layer: string): Promise<string> {
  const result = await postcss([
    tailwind({ ...config, content: [{ raw: fixture, extension: 'html' }] }),
  ]).process(`@tailwind ${layer};`, { from: undefined });
  return result.css;
}

const components = await compile('components');
const utilities = await compile('utilities');

function rule(css: string, selector: string): string {
  const at = css.indexOf(`${selector} {`);
  if (at < 0) throw new Error(`Tailwind не выпустил ${selector}`);
  return css.slice(at, css.indexOf('}', at));
}

describe('каждый стиль становится классом, который красит его целиком', () => {
  for (const [name, role] of Object.entries(roles)) {
    test(name, () => {
      const body = rule(components, `.type-${name}`);
      const step = steps[role.size];
      if (step === undefined) throw new Error(`стиль ${name}: нет ступени ${role.size}`);
      expect(body).toContain(`font-size: ${step.size}`);
      expect(body).toContain(`line-height: ${step.leading}`);
      expect(body).toContain(`letter-spacing: ${step.tracking}`);
      expect(body).toContain(`font-weight: ${weights[role.weight]}`);
      expect(body).toContain(`color: ${inkHex(role.ink)}`);
    });
  }
});

// Ступень — размер ВМЕСТЕ со своим интерлиньяжем и трекингом: `text-small` на значке
// или в строке журнала не наследует 20px от тела.
describe('каждая ступень ставит размер, интерлиньяж и трекинг одним классом', () => {
  for (const [name, step] of Object.entries(steps)) {
    test(name, () => {
      const body = rule(utilities, `.text-${name}`);
      expect(body).toContain(`font-size: ${step.size}`);
      expect(body).toContain(`line-height: ${step.leading}`);
      expect(body).toContain(`letter-spacing: ${step.tracking}`);
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
  expect(utilities).not.toContain('.font-semibold');
  expect(utilities).not.toContain('.font-bold');
});

// Стиль должен проигрывать утилите на том же элементе — это то, что делает
// `type-small text-danger` строкой ошибки. Tailwind ставит слой components перед
// utilities, поэтому доказательство — то, что стили выходят из `@tailwind components`.
test('стили выпускаются в слой components', () => {
  expect(utilities).not.toContain('.type-small');
  expect(components).toContain('.type-small');
});
