import { readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

import { expect, test } from 'vitest';

// Два свойства графа импортов, и оба тихие: сломав их, ничего не покрасишь неправильно —
// просто в какой-то момент константа окажется `undefined` или сборка перестанет быть
// замкнутой. Внимательностью такое не держат, поэтому гейт.

// От корня пакета, а не от `import.meta.url`: под Vitest последний не файловый адрес, и
// `new URL('.', …)` даёт путь, которого нет. Корень Vitest — этот пакет.
const ROOT = resolve(process.cwd(), 'src/shared/design-system');

function importsOf(file: string): string[] {
  return [...readFileSync(file, 'utf8').matchAll(/from '([^']+)'/g)].map((hit) => hit[1] ?? '');
}

// 1. Модули токенов не импортируют НИЧЕГО, кроме друг друга. На это свойство опираются
//    три вещи: `scripts/loadTokens.mjs` собирает их замкнутой сборкой (и её `require`
//    бросает, а не подставляет заглушку, именно чтобы нарушение было слышно),
//    `tailwind.config.ts` тянет их рядом с плагином Tailwind, а `shared/lib/cn.ts` — в
//    браузерный бандл.
test('модули токенов импортируют только друг друга', () => {
  const dir = join(ROOT, 'tokens');
  const offenders: string[] = [];
  for (const name of readdirSync(dir)) {
    if (!name.endsWith('.ts') || name.endsWith('.test.ts')) continue;
    for (const from of importsOf(join(dir, name))) {
      if (!from.startsWith('./')) offenders.push(`tokens/${name} -> ${from}`);
    }
  }
  expect(offenders).toEqual([]);
});

// 2. `cn.ts` тянет токены ГЛУБОКИМ путём, а не через баррель дизайн-системы. Через баррель
//    получался цикл: `cn.ts` → `design-system/index` → `recipes/*` → `cn.ts`. Vite такой
//    цикл разрешает молча, поэтому он и прожил до ревью — но молчание тут свойство
//    сборщика, а не кода: в цикле порядок инициализации зависит от того, кто вошёл первым.
//
//    Проверяется именно это направление, а не «токены ничего не импортируют»: цикл шёл
//    через `recipes/`, и первый тест его бы не увидел.
test('cn.ts не тянет дизайн-систему через баррель', () => {
  const cn = importsOf(resolve(process.cwd(), 'src/shared/lib/cn.ts'));
  const viaDesignSystem = cn.filter((from) => from.includes('shared/design-system'));

  expect(viaDesignSystem).not.toEqual([]);
  for (const from of viaDesignSystem) {
    expect(from).toMatch(/shared\/design-system\/tokens\//);
  }
});

// 3. Рецепты импортируют `cn` — это и есть вторая половина того цикла, и она законна:
//    рецепт собирает список классов, а разрешать их конфликты умеет только `cn`. Тест
//    закрепляет, что цикл разорван С ОДНОЙ стороны, и объясняет, с какой.
test('рецепты тянут cn, и потому cn не имеет права тянуть рецепты', () => {
  const dir = join(ROOT, 'recipes');
  const usesCn = readdirSync(dir)
    .filter((name) => name.endsWith('.ts') && name !== 'index.ts')
    .filter((name) => importsOf(join(dir, name)).some((from) => from.includes('shared/lib/cn')));

  expect(usesCn.length).toBeGreaterThan(0);
});

// Диалог, построенный вокруг таблицы, обязан быть шире пола таблицы — иначе таблицы в нём
// не будет вовсе, а будет раскладка карточками, которую никто не просил. Арифметикой в
// токене это не записано (926 = 880 + 36 поля + 10 запаса, и «запас» ролью не является),
// поэтому связь между двумя ступенями держит утверждение, а не выражение.
test('ширина диалога с таблицей больше пола таблицы', async () => {
  const { breakpoint, width } = await import('./spacing');
  expect(Number.parseInt(width.table, 10)).toBeGreaterThan(breakpoint.table);
});

// ── Семантический уровень закрыт с двух концов ──────────────────────────────────────
//
// Оба конца ломались молча и по-разному, и оба были найдены не гейтом:
//
//   • роль объявлена и НЕ доходит до класса. Таких было восемь: `background.muted`,
//     `background.inverse`, `content.inverse`, `content.disabled`, `action.disabled`,
//     `feedback.info.base`, `feedback.info.pressed`, `feedback.danger.pressed`. Каждая
//     выглядела частью системы и не была применима ничем; три последние стояли «для
//     симметрии» тонов.
//   • класс объявлен и обходит роль, ссылаясь прямо на примитив. Таких было три, и среди
//     них самый носимый класс приложения: `surface-card` брал `palette.white` напрямую,
//     то есть 143 сайта белой поверхности не проходили через уровень назначения вообще.
//
// Проверка идёт по ИСХОДНИКУ, а не по значениям. Сверка по значениям слепа именно там, где
// нужна: `content.disabled` и `content.subtle` — один и тот же серый, поэтому «значение
// носится» выполнялось бы для мёртвой ступени за счёт живой. Роль — это путь, а не цвет.
const TECHNICAL = new Set(['transparent', 'current', 'white', 'black']);
const GROUPS = ['background', 'content', 'border', 'action', 'feedback', 'inverse'];

async function semanticSource(): Promise<{ declared: string; projection: string }> {
  const { readFileSync } = await import('node:fs');
  const { join } = await import('node:path');
  const source = readFileSync(
    join(process.cwd(), 'src', 'shared', 'design-system', 'tokens', 'semantic.ts'),
    'utf8',
  );
  const at = source.indexOf('export const flatColors');
  return { declared: source.slice(0, at), projection: source.slice(at) };
}

/** Пути всех объявленных ролей: `content.subtle`, `feedback.info.tint`, … */
function declaredRoles(declared: string): string[] {
  const roles: string[] = [];
  for (const group of GROUPS) {
    const at = declared.indexOf(`export const ${group} = {`);
    if (at < 0) continue;
    const body = declared.slice(at, declared.indexOf('\n} as const;', at));
    let tone: string | null = null;
    for (const line of body.split('\n').slice(1)) {
      const opens = /^ {2}(\w+): \{$/.exec(line);
      if (opens) {
        tone = opens[1] ?? null;
        continue;
      }
      if (/^ {2}\},?$/.test(line)) {
        tone = null;
        continue;
      }
      const leaf = / {2,4}(\w+): /.exec(line);
      if (leaf) roles.push(tone === null ? `${group}.${leaf[1]}` : `${group}.${tone}.${leaf[1]}`);
    }
  }
  return roles;
}

test('каждая объявленная роль доходит до класса', async () => {
  const { declared, projection } = await semanticSource();
  const roles = declaredRoles(declared);

  // Если разбор перестанет находить роли, утверждение ниже выполнится на пустом списке.
  expect(roles.length).toBeGreaterThan(30);
  expect(roles.filter((role) => !projection.includes(role))).toEqual([]);
});

test('ни один класс не обходит роль ссылкой на примитив', async () => {
  const { projection } = await semanticSource();

  const offenders = projection
    .split(/\r?\n/)
    .map((line, at) => ({ line: line.trim(), at: at + 1 }))
    .filter(({ line }) => /^'?[\w-]+'?:\s*palette\./.test(line))
    .filter(({ line }) => !TECHNICAL.has(/^'?([\w-]+)'?:/.exec(line)?.[1] ?? ''))
    .map(({ line, at }) => `${String(at)}: ${line}`);

  expect(offenders).toEqual([]);
});
