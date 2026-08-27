// Ищет ступени, которые tailwind.config.ts объявляет, а приложение не носит.
//
// `design-tokens/no-raw-values` держит закрытым один конец: в компонент нельзя
// написать значение мимо шкалы. Другой конец до сих пор был открыт — в конфиг
// можно добавить ступень и не надеть её ни разу, и никто об этом не скажет.
// `ds:doc:check` тоже не скажет: он сверяет документ с конфигом, то есть честно
// опишет мёртвую ступень в таблице.
//
// Ступень считается ношеной, если её носит хоть одна утилита в `src` — класс
// вида `<приставка>-<имя>` — или если на неё ссылаются изнутри самого конфига:
// роль в `typeRole` называет рунг размера и краску, а `index.css` берёт значения
// через `theme('шкала.путь')`. Обе эти формы — настоящее ношение, и обе не
// выглядят как класс.
//
// Чего проверка НЕ умеет: приставки у шкал пересекаются с чужими именами
// (`shadow-pop` и `z-pop` — разные ступени с одним именем), поэтому пара
// «приставка + имя» разбирается целиком, а не по имени. Зато совпадение имён
// ВНУТРИ одной группы приставок она различить не может: если бы `text-body`
// оказалось и рунгом размера, и краской, ношение одного зачлось бы обоим. Такого
// пересечения сейчас нет, а если появится — проверка промолчит лишний раз, но не
// соврёт в другую сторону: мёртвой ступень объявляется только тогда, когда её
// имени нет в коде вовсе.
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = new URL('../', import.meta.url);
const CONFIG_PATH = new URL('tailwind.config.ts', ROOT);
const SRC_DIR = new URL('src/', ROOT);

// Шкала → приставки утилит, которые её тратят. Шкалы в конфиге ЗАМЕНЯЮТ шкалы
// Tailwind, а не расширяют их, поэтому каждое семейство утилит читает ровно одну
// шкалу и карта однозначна: `w-` берёт `width`, а не отступы.
const PREFIXES = {
  spacing:
    'p px py pt pr pb pl m mx my mt mr mb ml gap gap-x gap-y space-x space-y ' +
    'inset inset-x inset-y top right bottom left translate-x translate-y ' +
    'scroll-m scroll-p indent',
  size: 'size',
  height: 'h',
  width: 'w',
  minWidth: 'min-w',
  maxWidth: 'max-w',
  minHeight: 'min-h',
  maxHeight: 'max-h',
  fontSize: 'text',
  typeRole: 'type',
  lineHeight: 'leading',
  letterSpacing: 'tracking',
  borderRadius:
    'rounded rounded-t rounded-r rounded-b rounded-l rounded-tl rounded-tr rounded-bl rounded-br',
  boxShadow: 'shadow',
  transitionDuration: 'duration',
  transitionTimingFunction: 'ease',
  zIndex: 'z',
  colors:
    'bg text border border-x border-y border-t border-r border-b border-l ring ring-offset ' +
    'divide outline fill stroke from via to placeholder caret accent decoration shadow',
};

/* ── Разбор конфига ───────────────────────────────────────────────────────── */

// Нужны только ИМЕНА ступеней, не значения, поэтому блок берётся по балансу
// скобок, а имена — по отступу: первый уровень для плоской шкалы, второй для
// вложенной краски.
function block(src, key, indent) {
  const at = src.search(new RegExp(`^ {${indent}}${key}: \\{$`, 'm'));
  if (at < 0) throw new Error(`tailwind.config.ts: блок «${key}» не найден`);
  const open = src.indexOf('{', at);
  let depth = 0;
  let i = open;
  for (; i < src.length; i += 1) {
    if (src[i] === '{') depth += 1;
    else if (src[i] === '}' && (depth -= 1) === 0) break;
  }
  return src.slice(open, i);
}

function names(body, indent) {
  return [...body.matchAll(new RegExp(`^ {${indent}}'?([\\w$-]+)'?:`, 'gm'))].map((m) => m[1]);
}

// Краска вложена на рунг глубже: `primary.DEFAULT` носится как `bg-primary`,
// `primary.tint` — как `bg-primary-tint`. Плоская краска — своим именем.
function colorIds(src) {
  const body = block(src, 'colors', 6);
  const ids = [];
  for (const name of names(body, 8)) {
    const nested = new RegExp(`^ {8}'?${name}'?: \\{$`, 'm').test(body);
    if (!nested) {
      ids.push(name);
      continue;
    }
    for (const rung of names(block(body, `'?${name}'?`, 8), 10)) {
      ids.push(rung === 'DEFAULT' ? name : `${name}-${rung}`);
    }
  }
  return ids;
}

function readScales(src) {
  const scales = { colors: colorIds(src) };
  for (const key of Object.keys(PREFIXES)) {
    if (key !== 'colors') scales[key] = names(block(src, key, 4), 6);
  }
  return scales;
}

// Роль тратит рунг размера и краску по имени, изнутри конфига. Без этого шага
// рунг, который носят только роли, выглядел бы мёртвым.
function roleRefs(src) {
  const body = block(src, 'typeRole', 4);
  return [
    ...[...body.matchAll(/size: '([\w-]+)'/g)].map((m) => `fontSize.${m[1]}`),
    ...[...body.matchAll(/ink: '([\w-]+)'/g)].map((m) => `colors.${m[1]}`),
  ];
}

/* ── Чтение исходников ────────────────────────────────────────────────────── */

function sources(dir) {
  return readdirSync(dir, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile() && /\.(tsx?|css)$/.test(entry.name))
    .map((entry) => readFileSync(join(entry.parentPath, entry.name), 'utf8'))
    .join('\n');
}

// `theme('boxShadow.pop')`, `theme('colors.term.DEFAULT')` — ссылка на ступень
// по пути, а не классом. Приводится к тому же виду, что и id краски.
function themeRefs(text) {
  return [...text.matchAll(/theme\('([\w$]+)\.([\w$.-]+)'\)/g)].map(([, scale, path]) => {
    const rungs = path.split('.');
    if (scale !== 'colors') return `${scale}.${rungs[0]}`;
    return `colors.${rungs[1] === undefined || rungs[1] === 'DEFAULT' ? rungs[0] : rungs.join('-')}`;
  });
}

function worn(text, scale, name) {
  const prefix = PREFIXES[scale].split(' ').join('|');
  // Отрицательный отступ пишется `-mt-lg`: минус перед приставкой — часть класса,
  // а не граница. Справа граница нужна, иначе `w-col` зачлось бы `w-column`;
  // `/` пропускается — это модификатор прозрачности, `bg-primary/40`.
  return new RegExp(`(?:^|[^\\w-])-?(?:${prefix})-${name}(?![\\w-])`, 'm').test(text);
}

function main() {
  const src = readFileSync(CONFIG_PATH, 'utf8');
  const scales = readScales(src);
  const text = sources(SRC_DIR);
  const refs = new Set([...roleRefs(src), ...themeRefs(text)]);

  const dead = [];
  for (const [scale, list] of Object.entries(scales)) {
    for (const name of list) {
      // `DEFAULT` носится молча — `transition` и `ease` без имени, — и искать его
      // как класс нечего. `none` и `0` — не ступени, а отсутствие величины, ровно
      // как говорит про `boxShadow.none` сам конфиг; носителя у отсутствия может не
      // быть никогда, и снятие такой ступени открыло бы шкалу обратно вместо того,
      // чтобы её сузить.
      if (name === 'DEFAULT' || name === 'none' || name === '0') continue;
      if (refs.has(`${scale}.${name}`) || worn(text, scale, name)) continue;
      dead.push(`  ${scale}.${name}`);
    }
  }

  if (dead.length === 0) {
    process.stdout.write('tailwind.config.ts: каждую ступень кто-то носит\n');
    return 0;
  }
  process.stderr.write(
    `Шкалы объявляют ступени, которых нет в src (${dead.length}):\n${dead.join('\n')}\n` +
      'Ступень без единого носителя — это не запас, а лишний выбор: снять её из ' +
      'конфига или надеть.\n',
  );
  return 1;
}

process.exit(main());
