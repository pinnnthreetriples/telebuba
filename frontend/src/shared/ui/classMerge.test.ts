import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

import { expect, test } from 'vitest';

// Примитив, который СКЛЕИВАЕТ свой базовый класс с классом вызывающего, вместо того чтобы
// их слить, — это примитив, у которого перекрытие вызывающего может молча проиграть.
//
// Механика, а не стиль. В списке классов оказываются обе конфликтующие утилиты, и
// побеждает не последняя в атрибуте, а последняя в ВЫПУЩЕННОМ CSS: порядок утилит задаёт
// Tailwind, и он ничего не знает о том, кто из двоих был перекрытием. `text-right` в
// шапке таблицы годами выигрывал у `text-left` именно так — по счастливому порядку
// выпуска, а не потому, что кто-то это решил.
//
// `cn` (tailwind-merge) решает: конфликтующие группы разрешаются в пользу последнего
// аргумента, а вызывающий всегда последний. Пять примитивов склеивали строкой —
// `IconButton`, `Modal` (два места), `DataTable` (собственный `join`), `CollapsibleCard`
// (два места), — и все они это делали под одним объяснением: `cn` якобы затащил бы
// `shared/ui → shared/lib → баррель @tanstack`. Верно это было только про БАРРЕЛЬ:
// `@/shared/lib/cn` — листовой модуль, и `Card` с `FormField` импортировали его напрямую
// с самого начала.
//
// Проверка ищет не любой шаблонный литерал: внутренние — `${open ? 'rotate-180' : ''}` —
// законны, терять там нечего. Ищется интерполяция ИМЕНИ КЛАССА ВЫЗЫВАЮЩЕГО, то есть
// `${className}` и `${headerClassName}`: ровно та форма, в которой перекрытие приходит
// извне и может пропасть.
// Путь от рабочего каталога, а не от `import.meta.url`: под Vitest он не `file:`-URL, и
// разбор его как пути даёт `C:\src\...` на Windows. Тот же приём, что в `tokens.test.ts`.
const UI_DIR = join(process.cwd(), 'src', 'shared', 'ui');

const INTERPOLATED_CLASSNAME = /\$\{[^}]*[Cc]lassName[^}]*\}/;

test('ни один примитив shared/ui не склеивает класс вызывающего строкой', () => {
  const offenders: string[] = [];

  for (const name of readdirSync(UI_DIR)) {
    if (!name.endsWith('.tsx') || name.includes('.test.')) continue;
    const source = readFileSync(join(UI_DIR, name), 'utf8');
    source.split(/\r?\n/).forEach((line, at) => {
      if (INTERPOLATED_CLASSNAME.test(line)) offenders.push(`${name}:${String(at + 1)}`);
    });
  }

  expect(offenders).toEqual([]);
  // Если каталог перестанет читаться, список нарушителей тоже окажется пустым.
  expect(readdirSync(UI_DIR).filter((name) => name.endsWith('.tsx')).length).toBeGreaterThan(10);
});
