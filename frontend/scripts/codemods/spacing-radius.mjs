// Седьмой проход: отступы и скругления по Firecrawl.
//
// Переписывает классы ритма и радиуса со СТАРЫХ имён на новые — и только внутри строковых
// литералов и кусков шаблонных строк (`*.ts`, `*.tsx` в `src/`, `catalog/`, `stories/`).
// Комментарии, JSX-текст и всё, что не литерал, не трогаются: исходник разбирает
// компилятор TypeScript, а не регулярка по файлу.
//
//   node scripts/codemods/spacing-radius.mjs [--dry] [--assume=old|new] [пути…]
//
// Ритм. Ступени с точным эквивалентом в шкале Firecrawl переименовываются один в один и
// пикселей не меняют: `xs`→`1`, `sm`→`2`, `md`→`3`, `lg`→`4`, `2xl`→`6`, `page`→`8`,
// `empty`→`16`. Трёх старых ступеней в сетке 4px нет — `hair` 2px, `tight` 6px, `xl` 20px,
// — и они разводятся по РОЛИ, которую видно по приставке и соседним классам того же
// литерала (`ruleFor` ниже). `0` остаётся как был.
//
// Радиус. sm 6→sm 8, md 8→sm 8, lg 11→md 12, card 16→lg 16, full→full. Переименование
// идёт ОДНИМ проходом по таблице, а не цепочкой замен: старое `rounded-lg` (11px) обязано
// стать `rounded-md`, а старое `rounded-card` — `rounded-lg`, и вторая замена поверх первой
// превратила бы все поля в карточки.
//
// Повторный запуск. Ритм идемпотентен сам: новые имена числовые и среди старых их нет.
// Радиус — нет: `rounded-md` есть и в старой шкале, и в новой. Поэтому радиус в литерале
// переписывается, только когда видно, что литерал ещё старый:
//   1. в литерале есть старое имя ритма или `rounded-card` — старый; есть числовой ритм
//      (`p-2`, `gap-3`), которого до прохода не носил никто, — уже новый;
//   2. литерал без признаков — решает файл по тем же признакам;
//   3. файл без признаков (или с обоими) — решает `--assume`, по умолчанию состояние
//      самих токенов: в `primitives.ts` ещё есть `card` — дерево старое.
// Литерал, решённый по третьему шагу при новом дереве, не трогается и печатается в отчёт:
// это место, где ветку надо проверить глазами (или прогнать с `--assume=old <файл>`).
// На уже переписанном дереве отчёт поэтому не пуст — в нём файлы, где радиус есть, а
// ритма нет (`recipes/surfaces.ts`, `IconButton`), и это ожидаемо: переписано 0 классов.
import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import * as prettier from 'prettier';
import ts from 'typescript';

const ROOT = fileURLToPath(new URL('../../', import.meta.url));
const DEFAULT_DIRS = ['src', 'catalog', 'stories'];

// Файлы, которые говорят О шкале, а не носят её: фикстуры правила и `cn` проверяют
// именно старые и именно недопустимые написания, и переписать их значило бы стереть тест.
const EXCLUDE = [
  /[\\/]src[\\/]shared[\\/]design-system[\\/]tokens[\\/]/,
  /[\\/]src[\\/]shared[\\/]lib[\\/]cn\.test\.ts$/,
  /[\\/]src[\\/]shared[\\/]ui[\\/]designTokenRule\.test\.ts$/,
];

const SPACING_PREFIX = [
  'p',
  'px',
  'py',
  'pt',
  'pr',
  'pb',
  'pl',
  'ps',
  'pe',
  'm',
  'mx',
  'my',
  'mt',
  'mr',
  'mb',
  'ml',
  'ms',
  'me',
  'gap',
  'gap-x',
  'gap-y',
  'space-x',
  'space-y',
  'inset',
  'inset-x',
  'inset-y',
  'top',
  'right',
  'bottom',
  'left',
  'start',
  'end',
  'translate-x',
  'translate-y',
  'scroll-m',
  'scroll-mx',
  'scroll-my',
  'scroll-mt',
  'scroll-mr',
  'scroll-mb',
  'scroll-ml',
  'scroll-p',
  'scroll-px',
  'scroll-py',
  'scroll-pt',
  'scroll-pr',
  'scroll-pb',
  'scroll-pl',
  'indent',
].join('|');
const OLD_SPACING = 'hair|xs|tight|sm|md|lg|xl|2xl|page|empty';
const NEW_SPACING = '1|2|3|4|6|8|10|16|24';
const RADIUS_SIDE = 't|r|b|l|tl|tr|bl|br|s|e|ss|se|es|ee';

// Класс начинается с начала строки, после пробела, кавычки или двоеточия варианта. Точка,
// слэш и дефис слева означают, что это не класс (`max-w-md`, путь, ключ перевода).
// Исключение — селектор класса в тесте (`closest('.gap-md')`): точка, перед которой нет
// слова, начинает класс, а не продолжает путь или ключ.
const EDGE = String.raw`(?:(?<![\w./#-])|(?<=(?:^|[\s'"\x60>+~(,])\.))`;
const spacingToken = (names) =>
  new RegExp(String.raw`${EDGE}(-?)(${SPACING_PREFIX})-(${names})(?![\w./-])`, 'g');
const OLD_SPACING_RE = spacingToken(OLD_SPACING);
const NEW_SPACING_RE = spacingToken(NEW_SPACING);
const RADIUS_RE = new RegExp(
  String.raw`${EDGE}rounded(-(?:${RADIUS_SIDE}))?-(sm|md|lg|card)(?![\w-])`,
  'g',
);
const RETIRED_RADIUS_RE = new RegExp(
  String.raw`${EDGE}rounded(?:-(?:${RADIUS_SIDE}))?-card(?![\w-])`,
);

const EXACT = { xs: '1', sm: '2', md: '3', lg: '4', '2xl': '6', page: '8', empty: '16' };
const RADIUS = { sm: 'sm', md: 'sm', lg: 'md', card: 'lg' };

const has = (re, text) => {
  re.lastIndex = 0;
  const hit = re.test(text);
  re.lastIndex = 0;
  return hit;
};

// Контрол стоит на фиксированной высоте; его горизонтальное поле — это поле КНОПКИ.
const IN_A_CONTROL = /(?:^|[\s'"`:])h-(?:compact|field|control|touch|bar)(?![\w-])/;
const CONTROL_FILE = /[\\/]recipes[\\/]controls\.ts$/;

// Решение по роли для трёх ступеней вне сетки. Возвращает новое имя и короткую причину,
// которая попадает в отчёт.
function ruleFor(prefix, name, context, file) {
  if (name in EXACT) return [EXACT[name], 'точно'];
  const axis = prefix.replace(/^-/, '');
  if (name === 'hair') {
    // Столбики гистограммы стоят вплотную; 4px разорвали бы полосу, а волоска `px` в
    // сетке больше нет — щель между столбиками рисует их собственная граница.
    if (axis === 'gap' && /(?:^|\s)items-end(?![\w-])/.test(context)) return ['0', 'столбики'];
    // Вертикальное поле чипа снимается: плашка встаёт на высоту строки, 16px. Полоса с
    // прокруткой — исключение: её поле оставляет место крестику, вынесенному за угол.
    if ((axis === 'py' || axis === 'px') && !/overflow-[xy]?-?auto/.test(context))
      return ['0', 'поле чипа'];
    return ['1', 'зазор стопки'];
  }
  if (name === 'tight') {
    if (axis === 'gap-y') return ['2', 'ритм строк'];
    if (axis === 'gap' || axis === 'gap-x')
      return /(?:^|\s)flex-(?:col|wrap)(?![\w-])/.test(context)
        ? ['2', 'ритм строк']
        : ['1', 'иконка и подпись'];
    if (/^(?:px|py|pl|pr|m|mx|ml|mr)$/.test(axis)) return ['1', 'поле мелкого контрола'];
    return ['2', 'ритм строк'];
  }
  // xl
  if (/^gap(?:-[xy])?$/.test(axis)) return ['4', 'между группами'];
  if (axis === 'px' && (CONTROL_FILE.test(file) || IN_A_CONTROL.test(context)))
    return ['4', 'поле кнопки'];
  return ['6', 'поле карточки'];
}

/* ── Литералы ─────────────────────────────────────────────────────────────── */

// Каждый кусок строки — с контекстом: для шаблона это весь шаблон, потому что соседние
// классы того же списка нередко стоят по другую сторону `${…}`.
function literals(source) {
  const out = [];
  const visit = (node) => {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
      out.push({ start: node.getStart(source), end: node.end, context: node.text });
      return;
    }
    if (ts.isTemplateExpression(node)) {
      const context = node.getText(source);
      out.push({ start: node.head.getStart(source), end: node.head.end, context });
      for (const span of node.templateSpans) {
        visit(span.expression);
        out.push({ start: span.literal.getStart(source), end: span.literal.end, context });
      }
      return;
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return out;
}

function signals(text) {
  return {
    old: has(OLD_SPACING_RE, text) || RETIRED_RADIUS_RE.test(text),
    fresh: has(NEW_SPACING_RE, text),
  };
}

const verdict = ({ old, fresh }) => (old && !fresh ? 'old' : fresh && !old ? 'new' : null);

function tokensAreOld() {
  const primitives = readFileSync(
    join(ROOT, 'src/shared/design-system/tokens/primitives.ts'),
    'utf8',
  );
  const block = /export const radius = \{([\s\S]*?)\}/.exec(primitives);
  return block !== null && /\bcard:/.test(block[1]);
}

/* ── Проход ───────────────────────────────────────────────────────────────── */

function files(paths) {
  const found = [];
  const walk = (path) => {
    if (statSync(path).isDirectory()) {
      for (const entry of readdirSync(path)) {
        if (entry !== 'node_modules') walk(join(path, entry));
      }
    } else if (/\.tsx?$/.test(path) && !EXCLUDE.some((re) => re.test(path))) {
      found.push(path);
    }
  };
  for (const path of paths) walk(join(ROOT, path));
  return found;
}

function migrate(file, assumeOld, tally, ambiguous) {
  const text = readFileSync(file, 'utf8');
  const source = ts.createSourceFile(
    file,
    text,
    ts.ScriptTarget.Latest,
    true,
    file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const found = literals(source);
  // Признаки файла — только по литералам: старые имена в комментариях (история решений)
  // не говорят ничего о том, переписан ли сам код.
  const fileVerdict = verdict(signals(found.map((l) => text.slice(l.start, l.end)).join('\n')));
  const edits = [];
  for (const { start, end, context } of found) {
    const raw = text.slice(start, end);
    if (!has(OLD_SPACING_RE, raw) && !has(RADIUS_RE, raw)) continue;

    // Радиус решается по состоянию литерала ДО замены ритма в нём.
    let radiusOld = verdict(signals(context)) ?? fileVerdict;
    if (radiusOld === null) {
      radiusOld = assumeOld ? 'old' : 'new';
      if (!assumeOld && has(RADIUS_RE, raw))
        ambiguous.push(`${relative(ROOT, file)}: ${raw.trim()}`);
    }

    let next = raw.replace(OLD_SPACING_RE, (_, minus, prefix, name) => {
      const [to, why] = ruleFor(minus + prefix, name, context, file);
      const key = `ритм ${name}→${to} (${why})`;
      tally[key] = (tally[key] ?? 0) + 1;
      return `${minus}${prefix}-${to}`;
    });
    if (radiusOld === 'old') {
      next = next.replace(RADIUS_RE, (_, side = '', name) => {
        const key = `радиус ${name}→${RADIUS[name]}`;
        tally[key] = (tally[key] ?? 0) + 1;
        return `rounded${side}-${RADIUS[name]}`;
      });
    }
    if (next !== raw) edits.push({ start, end, next });
  }
  if (edits.length === 0) return false;
  let out = text;
  for (const { start, end, next } of edits.sort((a, b) => b.start - a.start)) {
    out = out.slice(0, start) + next + out.slice(end);
  }
  return out;
}

// Короткие имена укорачивают строки, и prettier после замены нередко сворачивает
// перенесённый атрибут обратно в одну строку. Переписанный файл форматируется сразу,
// чтобы проход оставлял дерево, которое проходит `npm run format`.
async function formatted(file, text) {
  const options = (await prettier.resolveConfig(file)) ?? {};
  return prettier.format(text, { ...options, filepath: file });
}

async function main(argv) {
  const dry = argv.includes('--dry');
  const assumeArg = argv.find((arg) => arg.startsWith('--assume='))?.slice('--assume='.length);
  if (assumeArg !== undefined && assumeArg !== 'old' && assumeArg !== 'new') {
    process.stderr.write('--assume принимает old или new\n');
    return 2;
  }
  const assumeOld = assumeArg === undefined ? tokensAreOld() : assumeArg === 'old';
  const paths = argv.filter((arg) => !arg.startsWith('--'));
  const tally = {};
  const ambiguous = [];
  let changed = 0;
  for (const file of files(paths.length > 0 ? paths : DEFAULT_DIRS)) {
    const out = migrate(file, assumeOld, tally, ambiguous);
    if (out === false) continue;
    changed += 1;
    if (!dry) writeFileSync(file, await formatted(file, out));
  }
  const lines = Object.entries(tally).sort(([a], [b]) => a.localeCompare(b, 'ru'));
  const total = lines.reduce((sum, [, n]) => sum + n, 0);
  process.stdout.write(
    `spacing-radius: ${dry ? 'нашёл' : 'переписал'} ${String(total)} классов в ${String(changed)} файлах ` +
      `(дерево без признаков считается ${assumeOld ? 'старым' : 'новым'})\n`,
  );
  for (const [key, n] of lines) process.stdout.write(`  ${String(n).padStart(4)}  ${key}\n`);
  if (ambiguous.length > 0) {
    process.stdout.write(
      `Радиус не тронут — по литералу и файлу не видно, старый он или новый (${String(ambiguous.length)}):\n`,
    );
    for (const line of ambiguous) process.stdout.write(`  ${line}\n`);
  }
  return 0;
}

process.exit(await main(process.argv.slice(2)));
