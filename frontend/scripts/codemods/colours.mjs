// Восьмой проход: один цвет — одно имя. Переписывает классы краски со СТАРЫХ имён на новые.
//
// Карта знает только старые имена, поэтому прогон идемпотентен и переносим: его можно
// повторить поверх другой ветки, и он тронет ровно то, что там ещё написано по-старому.
//
// Переписываются только строковые и шаблонные литералы (`'…'`, `"…"`, `` `…` `` и атрибуты
// JSX) — разбор идёт через компилятор TypeScript, а не регуляркой по файлу, поэтому проза в
// комментариях, которая называет прежние имена ради истории, остаётся как есть.
//
//   node scripts/codemods/colours.mjs          переписать
//   node scripts/codemods/colours.mjs --check  только сказать, что осталось (код 1, если есть)
//
// Имена короче прежних, поэтому после прогона строки надо переложить: `npx prettier --write .`
import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { join, relative } from 'node:path';

import ts from 'typescript';

const ROOT = new URL('../../', import.meta.url);
const DIRS = ['src', 'catalog', 'stories'];

// Фикстуры гейта называют ушедшие имена НАРОЧНО: они проверяют, что правило их ловит.
const SKIP = [/[\\/]designTokenRule\.test\.ts$/];

// Старое имя → новое. Каждая строка — одна краска, у которой было два имени.
const RENAME = {
  // #ffffff: белая поверхность. `white` оставался только под альфой над фотографией —
  // и это та же белая поверхность, просвечивающая.
  white: 'surface-card',
  // #ffffff: чернила на любой заливке — действие, тон, нейтральная, тёмная поверхность.
  'on-action': 'on-fill',
  'on-success': 'on-fill',
  'on-warning': 'on-fill',
  'on-danger': 'on-fill',
  'on-inverse': 'on-fill',
  'on-neutral': 'on-fill',
  // #0066ff: индикатор фокуса — та же заливка действия.
  focus: 'action-primary',
  // #eef4ff: подложка синего тона, она же наведение и выбранная плитка.
  'action-hover': 'info-tint',
  // Заглушка медиа: градиент между двумя существующими красками.
  'fallback-start': 'info-line',
  'fallback-end': 'line',
  // #f0eeeb → #f1efed: разделитель строк был в 1–2 единицах от `canvas`.
  'line-row': 'canvas',
};

// Утилиты краски. Длинные приставки раньше коротких, чтобы `border-t-` не прочиталось
// как `border-` с именем `t-…`.
const PREFIX = [
  'ring-offset',
  'border-x',
  'border-y',
  'border-t',
  'border-r',
  'border-b',
  'border-l',
  'divide-x',
  'divide-y',
  'placeholder',
  'decoration',
  'outline',
  'border',
  'divide',
  'accent',
  'shadow',
  'stroke',
  'caret',
  'ring',
  'fill',
  'from',
  'text',
  'via',
  'bg',
  'to',
];

// Приставка, у которой то же имя значит другую шкалу: `shadow-focus` — тень фокуса из
// `boxShadow`, а не краска `focus`.
const NOT_A_COLOUR = { shadow: new Set(['focus']) };

const OLD = Object.keys(RENAME)
  .sort((a, b) => b.length - a.length)
  .join('|');
// Перед утилитой — начало, пробел, кавычка, `:` варианта (`hover:`) или `!` важности;
// после — конец, пробел, кавычка, `}` или `/` альфы. Так `on-action` не цепляет
// `on-action-track`, а `bg-white` — `bg-white-ish`.
const CLASS = new RegExp(
  String.raw`(?<=^|[\s'"\`:!{}])(${PREFIX.join('|')})-(${OLD})(?=$|[\s'"\`}/$])`,
  'g',
);

// Сколько сайтов переехало с каждого имени: для отчёта и для таблицы в design-system.md.
const tally = Object.fromEntries(Object.keys(RENAME).map((name) => [name, 0]));

export function rewrite(text) {
  return text.replace(CLASS, (whole, prefix, name) => {
    if (NOT_A_COLOUR[prefix]?.has(name)) return whole;
    tally[name] += 1;
    return `${prefix}-${RENAME[name]}`;
  });
}

const LITERAL = new Set([
  ts.SyntaxKind.StringLiteral,
  ts.SyntaxKind.NoSubstitutionTemplateLiteral,
  ts.SyntaxKind.TemplateHead,
  ts.SyntaxKind.TemplateMiddle,
  ts.SyntaxKind.TemplateTail,
]);

function rewriteFile(path, source) {
  const kind = path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, kind);
  const edits = [];
  const visit = (node) => {
    if (LITERAL.has(node.kind)) {
      const start = node.getStart(file);
      const raw = source.slice(start, node.end);
      const next = rewrite(raw);
      if (next !== raw) edits.push({ start, end: node.end, next });
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  let out = source;
  for (const { start, end, next } of edits.sort((a, b) => b.start - a.start)) {
    out = out.slice(0, start) + next + out.slice(end);
  }
  return { out, count: edits.length };
}

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules') continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) yield* walk(full);
    else if (/\.tsx?$/.test(name) && !name.endsWith('.d.ts')) yield full;
  }
}

const check = process.argv.includes('--check');
const root = new URL('.', ROOT).pathname.replace(/^\/([A-Za-z]:)/, '$1');
let files = 0;
let literals = 0;
for (const dir of DIRS) {
  for (const path of walk(join(root, dir))) {
    if (SKIP.some((re) => re.test(path))) continue;
    const source = readFileSync(path, 'utf8');
    const { out, count } = rewriteFile(path, source);
    if (count === 0) continue;
    files += 1;
    literals += count;
    if (check) console.log(`  ${relative(root, path)}: ${String(count)}`);
    else writeFileSync(path, out);
  }
}
console.log(
  `${check ? 'осталось' : 'переписано'}: литералов ${String(literals)}, файлов ${String(files)}`,
);
for (const [name, n] of Object.entries(tally)) {
  if (n > 0) console.log(`  ${name} → ${RENAME[name]}: ${String(n)}`);
}
if (check && literals > 0) process.exitCode = 1;
