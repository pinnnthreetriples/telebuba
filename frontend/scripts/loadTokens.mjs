// Загружает `src/shared/design-system/tokens` в Node — и это конец разбору конфига
// текстом.
//
// До токенов гейты читали `tailwind.config.ts` регулярками по отступу, и в шапке
// генератора документации стояла причина: импорт .ts из Node требует загрузчика в
// зависимостях или флага снятия типов, а гейт, падающий от версии рантайма, хуже
// отсутствующего. Причина была верной, а цена — нет. Разбор по отступу означает, что
// перенос блока на два пробела ломает гейт; что значение, записанное в другой форме,
// пропадает молча; и что генератор документации умеет прочитать `'#f1efed'`, но не
// `background.canvas` — то есть ровно перестаёт работать, как только палитра получает
// уровень семантики.
//
// esbuild снимает оба возражения: он объявлен в devDependencies (а не подцеплен
// транзитивно через vite, который завтра может уехать на rolldown), собирает
// детерминированно и от версии Node не зависит. Токены не импортируют ничего, кроме друг
// друга, поэтому сборка замкнута и `require` внутри не нужен.
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { buildSync } from 'esbuild';

const REL = 'src/shared/design-system/tokens/index.ts';

// Точка входа ищется от МОДУЛЯ, а не от cwd, потому что гейты запускаются и через
// `npm run` из этого пакета, и через pre-commit из корня репозитория. Но `import.meta.url`
// указывает на файл только у нативного ESM: Vitest, загружая этот модуль через правило
// ESLint, отдаёт свой собственный адрес, и путь получается `/src/...` вместо
// `C:/…/frontend/src/...`. Путь не гипотетический — его проходит
// `designTokenRule.test.ts`. Поэтому кандидатов три, и выбирает не разбор адреса, а
// существование файла: единственная проверка, которая не врёт ни в одном из трёх запусков.
function entryPoint() {
  const candidates = [
    () => fileURLToPath(new URL(`../${REL}`, import.meta.url)),
    () => resolve(process.cwd(), REL),
    () => resolve(process.cwd(), `frontend/${REL}`),
  ];
  for (const candidate of candidates) {
    try {
      const path = candidate();
      if (existsSync(path)) return path;
    } catch {
      // Неподходящая схема адреса — не ошибка, а следующий кандидат.
    }
  }
  throw new Error(`loadTokens: ${REL} не найден ни от модуля, ни от ${process.cwd()}`);
}

function compile() {
  const built = buildSync({
    entryPoints: [entryPoint()],
    bundle: true,
    write: false,
    format: 'cjs',
    platform: 'node',
    target: 'node20',
  });
  const [output] = built.outputFiles;
  if (output === undefined) throw new Error('loadTokens: esbuild ничего не вернул');
  return output.text;
}

function evaluate(code) {
  const shell = { exports: {} };
  // `require` бросает, а не отсутствует: замкнутость сборки — свойство, на которое здесь
  // опираются, и молчаливая подстановка заглушки скрыла бы её нарушение.
  const forbid = (name) => {
    throw new Error(`loadTokens: токены не должны ничего импортировать, а просят «${name}»`);
  };
  new Function('module', 'exports', 'require', code)(shell, shell.exports, forbid);
  return shell.exports;
}

let cached;

/** Настоящий объект токенов — тот же, что импортирует приложение. */
export function loadTokens() {
  cached ??= evaluate(compile());
  return cached;
}
