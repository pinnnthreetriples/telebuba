import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

// Общий обход исходников для гейтов, которые читают ДЕРЕВО, а не значения.
//
// Таких гейтов три, и появились они по одной причине: часть решений нельзя проверить ни
// правилом ESLint, ни утверждением в jsdom. Правило читает списки классов и не видит
// вложенности; jsdom видит один отрендеренный случай, а не все места вызова. «Карточке не
// передают отступ» и «кольцо внутри кнопки собирает кнопка» — утверждения обо ВСЕХ местах
// вызова, поэтому читается текст.
//
// Обход здесь один, и это не преждевременное обобщение: без него у трёх гейтов было бы три
// копии одного разбора JSX, то есть ровно тот дефект, против которого построена вся
// дизайн-система. Второй разбор уже был написан и уже разошёлся с первым.
//
// `contrast.test.ts` держит свой обход и сюда не переезжает: он умеет больше — конец
// элемента, семантику «всегда применяется» у кусков шаблонного литерала, пропуск
// комментариев внутри тела, — и переносить работающий гейт на 380 строк ради общего имени
// значило бы платить риском за симметрию.

let cache: { path: string; source: string }[] | null = null;

/** Все `.tsx` приложения, кроме тестов, прочитанные один раз за прогон. */
export function tsxSources(): { path: string; source: string }[] {
  cache ??= readdirSync(join(process.cwd(), 'src'), { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith('.tsx'))
    .filter((entry) => !entry.name.includes('.test.'))
    .map((entry) => {
      const path = join(entry.parentPath, entry.name);
      return { path: path.slice(path.indexOf('src')), source: readFileSync(path, 'utf8') };
    });
  return cache;
}

/** Номер строки, на которой стоит индекс: для адреса в сообщении о нарушении. */
export function lineAt(source: string, index: number): number {
  return source.slice(0, index).split('\n').length;
}

/**
 * Собственный текст открывающего тега: от `<` до своего `>` или до первого вложенного
 * элемента.
 *
 * Ни то, ни другое по отдельности не годится. «Окном по строке» (`[^>]*`) обход обрывается
 * на `=>` в пропе-функции и молча пропускает всё, что записано дальше. «До своего `>`»
 * уходит внутрь дерева, переданного пропом (`header={<>…</>}`), и собирает чужие классы:
 * первая версия проверки отступов насчитала так в `LaunchCard` семь отступов, которые
 * принадлежат внутренним `div`.
 *
 * Комментарии пропускаются: апостроф в английской прозе внутри пропа иначе открывает
 * строку, которая проглатывает остаток файла.
 */
export function ownAttributes(source: string, start: number): string {
  let depth = 0;
  let quote = '';
  for (let i = start + 1; i < source.length; i += 1) {
    const c = source[i] ?? '';
    if (quote !== '') {
      if (c === '\\') i += 1;
      else if (c === quote) quote = '';
      continue;
    }
    if (c === '/' && source[i + 1] === '/') i = source.indexOf('\n', i);
    else if (c === '/' && source[i + 1] === '*') i = source.indexOf('*/', i) + 1;
    else if (c === '"' || c === "'" || c === '`') quote = c;
    else if (c === '{') depth += 1;
    else if (c === '}') depth -= 1;
    else if (c === '<' || (c === '>' && depth === 0)) return source.slice(start, i);
    if (i < start) break;
  }
  return source.slice(start);
}

/** Тела всех `<Tag>…</Tag>` файла, с учётом вложенности одноимённых. */
export function elementBodies(source: string, tag: string): { at: number; body: string }[] {
  const bodies: { at: number; body: string }[] = [];
  let depth = 0;
  let from = 0;
  for (const hit of source.matchAll(new RegExp(`</?${tag}\\b`, 'g'))) {
    if (hit[0].startsWith('</')) {
      depth -= 1;
      if (depth === 0) bodies.push({ at: from, body: source.slice(from, hit.index) });
    } else {
      if (depth === 0) from = hit.index;
      depth += 1;
    }
  }
  return bodies;
}

/** Строки в кавычках внутри куска исходника: списки классов, как их написали. */
export function quotedStrings(text: string): string[] {
  return [...text.matchAll(/'([^']*)'|"([^"]*)"/g)].map((hit) => hit[1] ?? hit[2] ?? '');
}
