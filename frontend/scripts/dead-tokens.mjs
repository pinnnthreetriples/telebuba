// Ищет ступени, которые дизайн-система объявляет, а приложение не носит.
//
// `design-tokens/no-raw-values` держит закрытым один конец: в компонент нельзя
// написать значение мимо шкалы. Другой конец до сих пор был открыт — в токены
// можно добавить ступень и не надеть её ни разу, и никто об этом не скажет.
// `ds:doc:check` тоже не скажет: он сверяет документ с токенами, то есть честно
// опишет мёртвую ступень в таблице.
//
// Ступень считается ношеной, если её носит хоть одна утилита в `src` — класс
// вида `<приставка>-<имя>` — или если на неё ссылаются изнутри самой системы:
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

import { colorIds, roleRefs, scaleNames } from './configScales.mjs';

const SRC_DIR = new URL('../src/', import.meta.url);

// Шкала → приставки утилит, которые её тратят. Шкалы в теме ЗАМЕНЯЮТ шкалы
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

/* ── Состав шкал ──────────────────────────────────────────────────────────── */

// Состав приходит из `configScales.mjs` — того же модуля, который читает правило
// `design-tokens/no-raw-values`. Держать его двумя копиями значило бы повторить ровно то
// расхождение, которое эта проверка ищет в самих токенах.

function readScales() {
  const scales = { colors: colorIds() };
  for (const key of Object.keys(PREFIXES)) {
    if (key !== 'colors') scales[key] = scaleNames(key);
  }
  return scales;
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
  const scales = readScales();
  const text = sources(SRC_DIR);
  const refs = new Set([...roleRefs(), ...themeRefs(text)]);

  const dead = [];
  for (const [scale, list] of Object.entries(scales)) {
    for (const name of list) {
      // `DEFAULT` носится молча — `transition` и `ease` без имени, — и искать его
      // как класс нечего. `none` и `0` — не ступени, а отсутствие величины, ровно
      // как говорят про `shadow.none` сами токены; носителя у отсутствия может не
      // быть никогда, и снятие такой ступени открыло бы шкалу обратно вместо того,
      // чтобы её сузить.
      if (name === 'DEFAULT' || name === 'none' || name === '0') continue;
      if (refs.has(`${scale}.${name}`) || worn(text, scale, name)) continue;
      dead.push(`  ${scale}.${name}`);
    }
  }

  if (dead.length === 0) {
    process.stdout.write('design-system/tokens: каждую ступень кто-то носит\n');
    return 0;
  }
  process.stderr.write(
    `Шкалы объявляют ступени, которых нет в src (${dead.length}):\n${dead.join('\n')}\n` +
      'Ступень без единого носителя — это не запас, а лишний выбор: снять её из ' +
      'src/shared/design-system/tokens или надеть.\n',
  );
  return 1;
}

process.exit(main());
