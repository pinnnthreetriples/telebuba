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
