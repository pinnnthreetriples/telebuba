import { describe, expect, test } from 'vitest';

import { compileClasses } from './tailwind.test-helpers';

// Шкалы закрыты не правилом линтера, а самим Tailwind: класс мимо шкалы не выпускает
// ничего. В Tailwind 3 это давала тема в корне конфига; в Tailwind 4 — сгенерированная
// тема, которая начинается с `--*: initial` и не объявляет `--spacing`. Обе половины
// проверяются здесь компиляцией настоящего `index.css`, потому что ни объект токенов, ни
// текст темы не говорят, что именно Tailwind из них сделает: умолчание, вернувшееся через
// запасной путь (`w-3` из ритма, `min-h-*` из шкалы высот), видно только в выпущенном CSS.

/** Классы, у которых Tailwind 4 не находит ступени, — и которые потому молчат. */
const SILENT = [
  // Ритм — ключи 0,1,2,3,4,6,8,16. Шаг мимо него не множитель `--spacing`, а ничто.
  'p-5',
  'px-7',
  'm-10',
  '-mt-5',
  'gap-12',
  'space-y-5',
  'top-5',
  'translate-x-5',
  // Размеры — свои шкалы. Ступень ритма в них не протекает: `p-3` красит 12px, `w-3` —
  // ничего, и это главное решение `spacing.ts`.
  'w-3',
  'h-4',
  'size-2',
  'min-w-4',
  'max-w-8',
  'leading-3',
  // Палитра, ступени шрифта, веса, интерлиньяж и трекинг Tailwind.
  'bg-blue-500',
  'text-red-600',
  'border-gray-200',
  'text-sm',
  'text-lg',
  'font-bold',
  'font-semibold',
  'font-serif',
  'leading-6',
  'leading-tight',
  'tracking-wide',
  // Радиусы, тени, размытия, анимации, кривые и контейнеры Tailwind.
  'rounded',
  'rounded-xl',
  'shadow',
  'shadow-lg',
  'drop-shadow-md',
  'inset-shadow-sm',
  'blur',
  'blur-sm',
  'backdrop-blur',
  'animate-pulse',
  'animate-spin',
  'ease-in-out',
  'max-w-md',
  'w-md',
  // Брейкпоинты, которых нет.
  'xl:p-3',
  '2xl:p-3',
];

const silentCss = await compileClasses(SILENT);

describe('класс мимо шкалы не выпускает ничего', () => {
  for (const cls of SILENT) {
    test(cls, () => {
      // Селектор в CSS экранирован (`xl\:p-3`) и не всегда стоит перед `{`:
      // `space-y-*` выходит как `:where(.space-y-5 > …)`. Поэтому ищется сам класс, за
      // которым нет продолжения имени.
      const escaped = cls.replace(/[:/.]/g, (char) => `\\${char}`);
      const selector = new RegExp(
        `\\.${escaped.replace(/[\\^$.*+?()[\]{}|-]/g, '\\$&')}(?![\\w-])`,
      );
      expect(silentCss).not.toMatch(selector);
    });
  }
});

/** Ступени, которые обязаны красить ровно своё значение. */
const LOUD: [string, string][] = [
  ['p-3', 'padding: 12px'],
  ['-mt-1', 'margin-top: calc(4px * -1)'],
  ['gap-16', 'gap: 64px'],
  ['top-2', 'top: 8px'],
  ['basis-4', 'flex-basis: 16px'],
  ['w-col', 'width: 150px'],
  ['h-control', 'height: 36px'],
  ['size-tile', 'width: 34px'],
  ['min-h-touch', 'min-height: 44px'],
  ['max-h-feed', 'max-height: 220px'],
  ['bg-canvas', 'background-color: #f1efed'],
  ['text-content-primary', 'color: #0b0b0c'],
  ['rounded-lg', 'border-radius: 16px'],
  ['z-pop', 'z-index: 20'],
  ['font-medium', 'font-weight: 500'],
  ['leading-none', 'line-height: 1'],
  ['tracking-code', 'letter-spacing: 0.18em'],
];

const loudCss = await compileClasses(LOUD.map(([cls]) => cls));

describe('ступень шкалы красит своё значение', () => {
  for (const [cls, declaration] of LOUD) {
    test(cls, () => {
      const at = loudCss.indexOf(`.${cls} {`);
      expect(at).toBeGreaterThanOrEqual(0);
      expect(loudCss.slice(at, loudCss.indexOf('}', at))).toContain(declaration);
    });
  }
});

// Чего тема закрыть не может: Tailwind 4 красит голые числа, дроби и ключевые слова без
// ступени (`z-50`, `duration-300`, `w-1/2`, `h-auto`), а `min-h-*`/`max-h-*` берут ступень
// у шкалы высот. Эти классы держит линтер — `designTokenRule.test.ts` проверяет каждый.
// Здесь записано, что они действительно красят: если однажды перестанут, правило линтера
// станет лишним, и об этом лучше узнать из теста, чем не узнать вовсе.
test('то, что держит не тема, а линтер, действительно красит', async () => {
  const css = await compileClasses(['z-50', 'duration-300', 'h-auto', 'min-h-control']);
  expect(css).toContain('z-index: 50');
  expect(css).toContain('transition-duration: 300ms');
  expect(css).toContain('height: auto');
  expect(css).toContain('min-height: 36px');
});
