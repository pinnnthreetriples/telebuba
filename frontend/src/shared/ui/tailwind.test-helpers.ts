import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { compile } from '@tailwindcss/node';

// Настоящий вход Tailwind приложения — `src/app/styles/index.css` с его темой, слоями и
// вариантами, — скомпилированный по заданному списку классов, а не по `@source`.
//
// Гейты, которым нужен ВЫПУЩЕННЫЙ CSS (переход без длительности, стиль текста не в том
// слое, класс мимо закрытой шкалы), спрашивают его, а не объект токенов: объект может
// быть верным, а CSS из него — нет. Именно так однажды выключились 25 переходов.
//
// От корня пакета, а не от `import.meta.url`: под Vitest последний не файловый адрес.
const STYLES = resolve(process.cwd(), 'src/app/styles');

export const ENTRY_CSS = readFileSync(resolve(STYLES, 'index.css'), 'utf8');

/** CSS, который Tailwind выпускает для `classes`, из `css` (по умолчанию — `index.css`). */
export async function compileClasses(classes: string[], css: string = ENTRY_CSS): Promise<string> {
  const compiler = await compile(css, {
    base: STYLES,
    onDependency: () => undefined,
  });
  return compiler.build(classes);
}

/** Тело правила `selector { … }` в выпущенном CSS; бросает, если правила нет. */
export function ruleBody(css: string, selector: string): string {
  const at = css.indexOf(`${selector} {`);
  if (at < 0) throw new Error(`Tailwind не выпустил ${selector}`);
  return css.slice(at, css.indexOf('}', at));
}
