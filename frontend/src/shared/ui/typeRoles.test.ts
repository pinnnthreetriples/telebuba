import postcss from 'postcss';
import tailwind from 'tailwindcss';
import { describe, expect, test } from 'vitest';

import config from '../../../tailwind.config';

// The roles are declared in the config and drawn by a plugin, so the thing worth
// asserting is the CSS Tailwind actually emits — not the object it was emitted from.
// Every expectation below is derived from the config's own rungs and colour tokens: a
// role whose ink is retuned has to stay correct here without the test being edited,
// and a role that stops resolving its token fails loudly instead of painting nothing.
type Role = {
  size: string;
  weight: string;
  ink: string;
  leading: string;
  tracking?: string;
  caps?: string;
};

const roles = (config.theme as { typeRole: Record<string, Role> }).typeRole;
const fontSize = config.theme.fontSize as Record<string, string>;
const content = config.theme.colors.content;

// Краска роли пишется так, как её пишет класс — `content-primary`, — а палитра рампу
// вкладывает. Спецслучая «краска без рунга» больше нет: у `content` каждая ступень
// названа, и одноимённого корня `ink` не осталось.
function inkHex(token: string): string {
  const rung = token.slice('content-'.length) as keyof typeof content;
  return content[rung];
}

// One compile for the whole suite: Tailwind only emits a component whose class appears
// in `content`, so the fixture names every role and the CSS below is the full set.
const fixture = Object.keys(roles)
  .map((name) => `type-${name}`)
  .join(' ');

async function compile(layer: string): Promise<string> {
  const result = await postcss([
    tailwind({ ...config, content: [{ raw: fixture, extension: 'html' }] }),
  ]).process(`@tailwind ${layer};`, { from: undefined });
  return result.css;
}

const css = await compile('components');

function rule(name: string): string {
  const at = css.indexOf(`.type-${name} {`);
  if (at < 0) throw new Error(`the plugin emitted no .type-${name}`);
  return css.slice(at, css.indexOf('}', at));
}

describe('every declared role becomes a utility that paints it', () => {
  for (const [name, role] of Object.entries(roles)) {
    test(name, () => {
      const body = rule(name);
      expect(body).toContain(`font-size: ${fontSize[role.size]}`);
      expect(body).toContain(`font-weight: ${role.weight}`);
      expect(body).toContain(`color: ${inkHex(role.ink)}`);
    });
  }
});

test('a role carries letter-spacing and case only where it declares them', () => {
  expect(rule('eyebrow')).toContain('letter-spacing: 0.04em');
  expect(rule('eyebrow')).toContain('text-transform: uppercase');
  expect(rule('page-title')).toContain('letter-spacing: -0.02em');
  expect(rule('caption')).not.toContain('letter-spacing');
  expect(rule('caption')).not.toContain('text-transform');
});

// Интерлиньяж — дело РОЛИ, но не дело рунга размера, и это два разных утверждения.
// Прежде здесь стояло «ни одна роль не ставит интерлиньяж» с доводом, что роль с ним
// «переверстала бы каждый сайт, куда попала». Довод верен ровно для того значения, которое
// сайты уже носят: у всех двенадцати ролей он `body` — то самое, что они наследовали от
// `body`, — поэтому не переверстал ни одного. Зато роль перестала зависеть от предка:
// `type-caption` внутри `leading-log` разъезжался на 1.85 и выглядел не собой.
//
// Рунг размера при этом остаётся голой строкой (не парой `[размер, интерлиньяж]`): на нём
// стоит `text-*`, и вписать туда интерлиньяж значило бы переверстать сайты, которые взяли
// рунг, а не роль.
test('каждая роль называет свой интерлиньяж, и это ступень шкалы', () => {
  const scale = config.theme.lineHeight as Record<string, string>;
  for (const [name, role] of Object.entries(roles)) {
    const declared = scale[role.leading];
    expect(declared, `роль ${name} ссылается на несуществующую ступень`).toBeDefined();
    expect(rule(name)).toContain(`line-height: ${String(declared)}`);
  }
});

// The reason this is `addComponents` and not a utility: a role has to lose to a utility
// on the same element, which is what makes `type-caption text-danger` an error line and
// `type-card-title font-bold` a heading someone still has to argue for. Tailwind orders
// the components layer before the utilities layer, so the proof is that the roles come
// out of `@tailwind components` at all — a utility would have been dropped here.
test('the roles are emitted into the components layer', async () => {
  expect(await compile('utilities')).not.toContain('.type-caption');
  expect(css).toContain('.type-caption');
});
