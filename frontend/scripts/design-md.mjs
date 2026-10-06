// Собирает `docs/DESIGN.md` и `docs/design-md.html` — дизайн-систему в формате DESIGN.md
// (как её оформляет ui-skills.com: YAML-токены сверху, короткие правила прозой снизу).
//
// Зачем второй документ рядом с `design-system.html`: тот — канон с историей решений и
// живыми примерами, его читают, когда спорят о ступени. Этот — спецификация: одна
// страница, где видно ВСЁ, что можно поменять, и куда смотреть, чтобы поменять. Его же
// читают агенты — формат DESIGN.md для этого и придуман.
//
// Руками здесь не написано ни одного значения. Токены импортируются (`loadTokens.mjs`),
// а состав компонентов — кнопки, поля, поверхности, плашки — читается из их собственных
// наборов (`VARIANT`, `SIZE`, `SURFACE`, `TONE`) и переводится из классов Tailwind в
// ссылки на токены: `bg-action-primary` → `{colors.action-primary}`. Поменяли рецепт или
// токен — документ разошёлся, и `ds:doc:check` падает, пока его не соберут заново.
//
// Руками написаны только подписи у ступеней и компонентов: зачем ступень нужна. У цветов и
// ролей текста подписей нет намеренно — имя (`action-primary`, `type-h3`) уже
// говорит, где они стоят, а строка прозы под каждым цветом была шумом.
import { readFileSync, writeFileSync } from 'node:fs';

import { colorIds, tokens } from './configScales.mjs';
import { renderPage } from './design-md-page.mjs';

const ROOT = new URL('../', import.meta.url);
const MD_PATH = new URL('docs/DESIGN.md', ROOT);
const HTML_PATH = new URL('docs/design-md.html', ROOT);

const read = (rel) => readFileSync(new URL(rel, ROOT), 'utf8').replace(/\r\n/g, '\n');

/* ── Наборы компонентов из исходников ─────────────────────────────────────── */

// `const NAME = { … }` (с типом или без) → сам объект. Наборы записаны чистыми
// литералами из строк и вложенных объектов, поэтому их вычисляет `Function`, а не
// регулярка по строкам: перенос значения на следующую строку (`dashed:`) её бы обманул.
function readSet(rel, name) {
  const source = read(rel);
  const head = new RegExp(`const ${name}\\b[^=]*=\\s*\\{`).exec(source);
  if (head === null) throw new Error(`design-md: в ${rel} нет набора ${name}`);
  let depth = 0;
  const from = head.index + head[0].length - 1;
  for (let i = from; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    if (source[i] === '}') depth -= 1;
    if (depth === 0) {
      try {
        return new Function(`return (${source.slice(from, i + 1)});`)();
      } catch (error) {
        throw new Error(`design-md: набор ${name} в ${rel} не литерал: ${error.message}`);
      }
    }
  }
  throw new Error(`design-md: набор ${name} в ${rel} не закрыт`);
}

const UI = 'src/shared/ui';
const RECIPES = 'src/shared/design-system/recipes';
const sets = {
  buttonVariant: readSet(`${UI}/Button.tsx`, 'VARIANT'),
  buttonSize: readSet(`${UI}/Button.tsx`, 'SIZE'),
  controlHeight: readSet(`${RECIPES}/controls.ts`, 'CONTROL_HEIGHT'),
  controlText: readSet(`${RECIPES}/controls.ts`, 'CONTROL_TEXT'),
  buttonPad: readSet(`${RECIPES}/controls.ts`, 'BUTTON_PAD'),
  fieldPad: readSet(`${RECIPES}/controls.ts`, 'FIELD_PAD'),
  shape: readSet(`${RECIPES}/controls.ts`, 'SHAPE'),
  inputTone: readSet(`${UI}/Input.tsx`, 'TONE'),
  surface: readSet(`${RECIPES}/surfaces.ts`, 'SURFACE'),
  feedbackTone: readSet(`${RECIPES}/feedback.ts`, 'TONE'),
  badgeSize: readSet(`${UI}/Badge.tsx`, 'SIZE'),
};
const cardPadding = /className = '([^']+)'/.exec(read(`${UI}/Card.tsx`))?.[1] ?? '';

/* ── Класс Tailwind → ссылка на токен ─────────────────────────────────────── */

const COLORS = new Set(colorIds());

// Разбираются только приставки, которыми написаны рецепты. Остальное (раскладка,
// переходы, фокус) — поведение, а не значение, и в спецификацию не попадает.
function propsOf(classes) {
  const props = {};
  for (const raw of classes.split(/\s+/).filter(Boolean)) {
    const [state, cls] = raw.includes(':') ? raw.split(':') : ['', raw];
    if (state !== '' && state !== 'hover') continue;
    const put = (key, value) => {
      props[state === 'hover' ? `hover${key[0].toUpperCase()}${key.slice(1)}` : key] = value;
    };
    let m;
    if ((m = /^bg-(.+)$/.exec(cls)) && COLORS.has(m[1])) put('backgroundColor', `{colors.${m[1]}}`);
    else if ((m = /^text-(.+)$/.exec(cls)) && COLORS.has(m[1]))
      put('textColor', `{colors.${m[1]}}`);
    else if ((m = /^text-(.+)$/.exec(cls)) && m[1] in tokens.fontSize)
      put('fontSize', `{fontSize.${m[1]}}`);
    else if (cls === 'border') put('borderWidth', '1px');
    else if (cls === 'border-dashed') put('borderStyle', 'dashed');
    else if ((m = /^border-(.+)$/.exec(cls)) && COLORS.has(m[1]))
      put('borderColor', `{colors.${m[1]}}`);
    else if ((m = /^font-(.+)$/.exec(cls)) && m[1] in tokens.fontWeight)
      put('fontWeight', Number(tokens.fontWeight[m[1]]));
    else if ((m = /^rounded-(.+)$/.exec(cls)) && m[1] in tokens.radius)
      put('rounded', `{rounded.${m[1]}}`);
    else if ((m = /^h-(.+)$/.exec(cls)) && m[1] in tokens.height) put('height', `{height.${m[1]}}`);
    else if ((m = /^px-(.+)$/.exec(cls)) && m[1] in tokens.rhythm)
      put('paddingX', `{spacing.${m[1]}}`);
    else if ((m = /^py-(.+)$/.exec(cls)) && m[1] in tokens.rhythm)
      put('paddingY', `{spacing.${m[1]}}`);
    else if ((m = /^shadow-(.+)$/.exec(cls)) && m[1] in tokens.shadow)
      put('shadow', `{shadows.${m[1]}}`);
  }
  return props;
}

/* ── Цвета ────────────────────────────────────────────────────────────────── */

function flatten(scale) {
  const out = {};
  for (const [name, value] of Object.entries(scale)) {
    if (typeof value === 'string') out[name] = value;
    else
      for (const [rung, v] of Object.entries(value))
        out[rung === 'DEFAULT' ? name : `${name}-${rung}`] = v;
  }
  return out;
}

const COLOR_GROUPS = [
  { title: 'Действие', test: (id) => id.startsWith('action') },
  { title: 'Основа', test: (id) => /^(canvas|surface|black|scrim|veil)/.test(id) },
  { title: 'Текст', test: (id) => id.startsWith('content') },
  { title: 'Чернила на заливке', test: (id) => id.startsWith('on-') },
  { title: 'Линии', test: (id) => id.startsWith('line') },
  { title: 'Смысл', test: (id) => /^(info|success|warning|danger)/.test(id) },
  { title: 'Тёмная поверхность', test: (id) => id.startsWith('term') },
];

function colorGroups() {
  const all = Object.entries(flatten(tokens.flatColors)).filter(
    ([id]) => id !== 'transparent' && id !== 'current',
  );
  const groups = COLOR_GROUPS.map((g) => ({ title: g.title, items: [] }));
  const rest = { title: 'Прочее', items: [] };
  for (const [id, value] of all) {
    const at = COLOR_GROUPS.findIndex((g) => g.test(id));
    (at < 0 ? rest : groups[at]).items.push({ id, value });
  }
  return [...groups, rest].filter((g) => g.items.length > 0);
}

/* ── Типографика ──────────────────────────────────────────────────────────── */

function typeRoles() {
  return Object.entries(tokens.typeRole).map(([name, role]) => ({
    name,
    props: {
      fontFamily: 'Inter',
      fontSize: tokens.typeScale[role.size].size,
      fontWeight: Number(tokens.fontWeight[role.weight]),
      lineHeight: tokens.typeScale[role.size].leading,
      letterSpacing: tokens.typeScale[role.size].tracking,
      color: `{colors.${role.ink}}`,
    },
  }));
}

// Роли, сведённые к стилям: размер, вес, интерлиньяж, трекинг и регистр. Цвет в стиль не
// входит — роли, которые отличаются только серым, это ОДИН стиль, и страница показывает
// его одной карточкой с несколькими ролями под ней. Сколько карточек — столько ступеней
// иерархии на самом деле, а не столько, сколько имён.
function typeStyles(roles) {
  const byKey = new Map();
  for (const role of roles) {
    const { fontSize, fontWeight, lineHeight, letterSpacing } = role.props;
    const style = { fontSize, fontWeight, lineHeight, letterSpacing };
    const key = JSON.stringify(style);
    if (!byKey.has(key)) byKey.set(key, { style, roles: [] });
    byKey.get(key).roles.push({ name: role.name, color: role.props.color });
  }
  return [...byKey.values()].sort(
    (x, y) =>
      parseFloat(y.style.fontSize) - parseFloat(x.style.fontSize) ||
      y.style.fontWeight - x.style.fontWeight,
  );
}

/* ── Шкалы с подписями ────────────────────────────────────────────────────── */

const SPACING_NOTE = {
  1: 'Иконка и подпись, поле мелкого контрола',
  2: 'Внутри группы: подпись и поле, строки',
  3: 'Строки и поля внутри карточки',
  4: 'Между группами, поле кнопки',
  6: 'Поле карточки и диалога',
  8: 'Между секциями, поля страницы',
  16: 'Пустое состояние',
};
const RADIUS_NOTE = {
  sm: 'Контрол в коробке, чип, мелкая плашка',
  md: 'Поле ввода, панель, меню, вложенная карточка',
  lg: 'Карточка и диалог',
  full: 'Кнопка, плашка, аватар',
};
const SHADOW_NOTE = {
  pop: 'Всплывающее: диалог, панель, меню',
  ring: 'Тонкая обводка тенью',
  thumb: 'Ползунок, который тянут',
  focus: 'Свечение фокуса поля',
  seg: 'Поднятый сегмент переключателя',
  pill: 'Синяя пилюля вкладок',
};
const DURATION_NOTE = {
  state: 'Наведение, смена краски',
  enter: 'Что-то приезжает: тост, шторка',
  swap: 'Вещь меняется на другую на своём месте',
  reveal: 'Панель раскрывается',
  spin: 'Кольцо крутится',
  roll: 'Значение доезжает до себя',
  pulse: 'Вещь дышит на месте',
  stagger: 'Сдвиг второй половины подмены',
};
const EASING_NOTE = {
  out: 'Резко замедляется и встаёт',
  spring: 'Слегка перелетает и садится',
  linear: 'Только вращение',
  breathe: 'Уходит и возвращается',
};

const scaleOf = (scale, notes, skip = []) =>
  Object.entries(scale)
    .filter(([name]) => !skip.includes(name))
    .map(([name, value]) => ({ name, value: String(value), note: notes[name] ?? '' }));

/* ── Компоненты ───────────────────────────────────────────────────────────── */

const VARIANT_NOTE = {
  primary: 'Одно главное действие экрана',
  neutral: 'Залитое чернильное: «Остановить»',
  secondary: 'Всё рядом с главным действием (по умолчанию)',
  danger: 'Главное действие, которое что-то разрушает',
  ghost: 'Без коробки, пока не наведёшь',
  dashed: 'Добавить ещё один в список над ним',
  dashedMuted: 'Добавить чип в конец ряда',
};
const SURFACE_NOTE = {
  card: 'Карточка на странице',
  dialog: 'Модальное окно',
  panel: 'Всплывающая панель, меню',
  inset: 'Вложенный блок внутри карточки',
  inverse: 'Тёмная поверхность: журнал',
};

const kebab = (name) => name.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);

function buttonShape(size) {
  return propsOf(
    [
      sets.controlHeight[size],
      sets.buttonPad[size],
      sets.controlText[size],
      sets.shape.pill,
      sets.buttonSize[size].weight,
    ].join(' '),
  );
}

function fieldShape(size) {
  return propsOf(
    [
      'border bg-surface-card',
      sets.inputTone.default,
      sets.controlHeight[size],
      sets.fieldPad[size],
      sets.controlText[size],
      sets.shape[size === 'xs' ? 'inset' : 'field'],
    ].join(' '),
  );
}

function components() {
  const list = [];
  for (const [variant, classes] of Object.entries(sets.buttonVariant)) {
    list.push({
      group: 'button',
      name: `button-${kebab(variant)}`,
      note: VARIANT_NOTE[variant] ?? '',
      label:
        {
          primary: 'Сохранить',
          neutral: 'Остановить',
          danger: 'Удалить',
          ghost: 'Подробнее',
          dashed: 'Добавить кампанию',
          dashedMuted: 'Добавить канал',
        }[variant] ?? 'Отмена',
      props: { ...propsOf(classes), ...buttonShape('md') },
    });
  }
  for (const size of Object.keys(sets.buttonSize)) {
    if (size === 'md') continue;
    list.push({
      group: 'button-size',
      name: `button-${size}`,
      note: `Размер ${size}`,
      props: buttonShape(size),
    });
  }
  list.push({ group: 'input', name: 'input', note: 'Поле ввода (md)', props: fieldShape('md') });
  for (const size of ['sm', 'xs']) {
    list.push({
      group: 'input',
      name: `input-${size}`,
      note: `Поле ввода ${size}`,
      props: fieldShape(size),
    });
  }
  list.push({
    group: 'input',
    name: 'input-flat',
    note: 'Поле, в которое не печатают: факт на экране',
    props: { ...fieldShape('md'), ...propsOf(sets.inputTone.flat) },
  });
  for (const [name, classes] of Object.entries(sets.surface)) {
    list.push({
      group: 'surface',
      name,
      note: SURFACE_NOTE[name] ?? '',
      props: propsOf(name === 'card' ? `${classes} ${cardPadding}` : classes),
    });
  }
  for (const [tone, parts] of Object.entries(sets.feedbackTone)) {
    list.push({
      group: 'badge',
      name: `badge-${tone}`,
      note: `Плашка «${tone}»`,
      props: propsOf(`${parts.tint} ${parts.ink} rounded-full font-medium ${sets.badgeSize.xs}`),
    });
  }
  return list;
}

// Куда идти, чтобы поменять. Пути — от `src/`, и каждый проверяется на существование
// при сборке: переезд файла роняет генерацию, а не оставляет в документе мёртвую ссылку.
const HOW_TO = [
  ['Цвет везде', 'shared/design-system/tokens/primitives.ts', 'palette'],
  ['Что считается «действием», «ошибкой»', 'shared/design-system/tokens/semantic.ts'],
  ['Сетку отступов', 'shared/design-system/tokens/spacing.ts', 'rhythm'],
  ['Высоту кнопок и полей', 'shared/design-system/tokens/spacing.ts', 'height'],
  ['Шрифты и роли текста', 'shared/design-system/tokens/typography.ts'],
  ['Скругления и тени', 'shared/design-system/tokens/primitives.ts', 'radius, shadow'],
  ['Форму и поля всех контролов', 'shared/design-system/recipes/controls.ts'],
  ['Заливку вариантов кнопки', 'shared/ui/Button.tsx', 'VARIANT'],
  ['Карточки, панели, диалоги', 'shared/design-system/recipes/surfaces.ts', 'SURFACE'],
  ['Тона плашек и уведомлений', 'shared/design-system/recipes/feedback.ts', 'TONE'],
];

function howTo() {
  return HOW_TO.map(([what, file, symbol]) => {
    const source = read(`src/${file}`);
    if (symbol !== undefined) {
      for (const name of symbol.split(', ')) {
        if (!new RegExp(`\\b(const|function) ${name}\\b`).test(source)) {
          throw new Error(`design-md: в src/${file} нет ${name}`);
        }
      }
    }
    return [what, `src/${file}${symbol === undefined ? '' : ` → ${symbol}`}`];
  });
}

/* ── Сборка спецификации ──────────────────────────────────────────────────── */

function buildSpec() {
  const comps = components();
  const usedHeights = new Set(
    comps.map((c) => /^\{height\.(.+)\}$/.exec(String(c.props.height ?? ''))?.[1]).filter(Boolean),
  );
  return {
    colors: colorGroups(),
    typography: typeRoles(),
    typeStyles: typeStyles(typeRoles()),
    fontFamily: { sans: tokens.font.sans.join(', '), mono: tokens.font.mono.join(', ') },
    spacing: scaleOf(tokens.rhythm, SPACING_NOTE, ['0']),
    rounded: scaleOf(tokens.radius, RADIUS_NOTE),
    shadows: scaleOf(tokens.shadow, SHADOW_NOTE, ['none']),
    height: scaleOf(tokens.height, {}).filter((h) => usedHeights.has(h.name)),
    duration: scaleOf(tokens.duration, DURATION_NOTE, ['DEFAULT']),
    easing: scaleOf(tokens.easing, EASING_NOTE, ['DEFAULT']),
    breakpoint: scaleOf(tokens.breakpoint, {}),
    pressScale: tokens.pressScale.press,
    components: comps,
    howTo: howTo(),
  };
}

/* ── DESIGN.md ────────────────────────────────────────────────────────────── */

function yamlValue(value) {
  const text = String(value);
  if (typeof value === 'number' || /^-?[\d.]+(px|em|ms)?$/.test(text)) return text;
  return `"${text.replace(/"/g, '\\"')}"`;
}
const yamlKey = (key) => (/^[\w-]+$/.test(key) ? key : `"${key}"`);

function renderFrontmatter(spec) {
  const out = [
    '---',
    'version: alpha',
    'name: Telebuba',
    'description: Дизайн-система Telebuba — операторского дашборда для Telegram. Тёплый серый фон, белые карточки, один синий для действия и тона смысла с подложкой и рамкой. Светлая тема — единственная. Файл собран из src/shared/design-system командой npm run ds:doc; правка руками будет перезаписана.',
    'colors:',
  ];
  for (const group of spec.colors) {
    out.push(`  # ${group.title}`);
    for (const c of group.items) out.push(`  ${yamlKey(c.id)}: ${yamlValue(c.value)}`);
  }
  out.push(
    'fontFamily:',
    `  sans: ${yamlValue(spec.fontFamily.sans)}`,
    `  mono: ${yamlValue(spec.fontFamily.mono)}`,
  );
  out.push('typography:');
  for (const role of spec.typography) {
    out.push(`  ${role.name}:`);
    for (const [k, v] of Object.entries(role.props)) out.push(`    ${k}: ${yamlValue(v)}`);
  }
  const flat = (title, list) => {
    out.push(`${title}:`);
    for (const item of list) out.push(`  ${yamlKey(item.name)}: ${yamlValue(item.value)}`);
  };
  flat('spacing', spec.spacing);
  flat('rounded', spec.rounded);
  flat('shadows', spec.shadows);
  flat('height', spec.height);
  flat('duration', spec.duration);
  flat('easing', spec.easing);
  flat(
    'breakpoints',
    spec.breakpoint.map((b) => ({ ...b, value: `${b.value}px` })),
  );
  out.push('components:');
  for (const c of spec.components) {
    out.push(`  ${c.name}:`);
    for (const [k, v] of Object.entries(c.props)) out.push(`    ${k}: ${yamlValue(v)}`);
  }
  out.push('---');
  return out.join('\n');
}

function renderBody(spec) {
  const px = (scale, name) => scale.find((s) => s.name === name)?.value ?? '?';
  const rhythm = spec.spacing.map((s) => s.value.replace('px', '')).join(', ');
  const control = spec.height.find((h) => h.name === 'control')?.value ?? '?';
  return `
# Telebuba

## Обзор

Telebuba — операторский дашборд: аккаунты, прокси, прогрев, нейрокомментинг, нейрошиллинг. Интерфейс плотный и спокойный: белые карточки лежат на тёплом сером фоне, иерархию дают тон поверхности и рамка, а цвет несёт смысл. Синий \`action-primary\` — это действие; зелёный, янтарный и красный — исход и состояние. Тёмная поверхность \`term\` одна — журнал и подсказки.

Это закрытый набор. Каждое значение интерфейса берётся отсюда: сырой hex или произвольное \`[7px]\` в коде — ошибка линтера, а ступень, которую никто не носит, — ошибка гейта \`ds:dead\`. Значение меняют в \`src/shared/design-system/tokens/\`, и оно меняется везде.

## Цвета

Уровень 1 — палитра (\`primitives.ts\`): сырые краски, каждая записана один раз. Уровень 2 — назначение (\`semantic.ts\`): имена из этого файла ссылаются на палитру. Класс называет назначение (\`bg-surface-card\`, \`text-on-fill\`), а не краску. Одна краска — одно имя: синонимов в наборе нет, и \`tokens.test.ts\` это проверяет. Единственная пара с одним значением — \`surface-card\` и \`on-fill\` (оба белые): гейт контраста узнаёт пару по имени, и только разные имена оставляют белое на белом измеримым.

- **Основа.** \`canvas\` — под предметом, всё заполняемое и разделитель строк, \`surface-card\` — сам предмет (карточка, диалог, поле; под альфой — белое над фотографией), \`surface\` — шаг от белого внутри предмета.
- **Текст.** \`content-primary\` → \`secondary\` → \`muted\` → \`subtle\`. Последние два стоят на пороге AA: светлее не бывает.
- **Действие.** \`action-primary\` заливает главную кнопку и рисует кольцо фокуса, \`action-pressed\` — нажатие, \`info-tint\` — наведение на незалитое и выбранная плитка.
- **Смысл.** У каждого тона: основной (текст, иконка), \`-tint\` (подложка), \`-line\` (рамка подложки), \`-deep\` (текст на подложке, когда основной не проходит по контрасту).
- **Чернила на заливке.** \`on-fill\` — надпись и иконка на любой заливке: действие, тон, серый, тёмная поверхность. Пол контраста держит измерение, а не имя: на янтаре надпись стоит только на \`warning-deep\`.

## Типографика

Inter набирает весь интерфейс, JetBrains Mono — код, идентификаторы и журнал. Пять ступеней: ${Object.entries(
    tokens.typeScale,
  )
    .map(([name, step]) => `\`${name}\` ${step.size}/${step.leading}`)
    .join(
      ', ',
    )}. Каждый размер — один уровень иерархии, интерлиньяж кратен 4px, трекинг оптический (Inter Dynamic Metrics) и входит в ступень.

Весов два: ${Object.entries(tokens.fontWeight)
    .map(([name, value]) => `\`font-${name}\` ${value}`)
    .join(
      ', ',
    )}. 400 — то, что читают, 500 — то, что называют. Иерархию держит размер, а не жирность.

Страница называет **стиль**: ${Object.keys(tokens.typeRole)
    .map((name) => `\`type-${name}\``)
    .join(
      ', ',
    )}. Стиль несёт ступень, вес и краску по умолчанию; другой цвет пишется утилитой поверх: \`type-small text-danger\`. Числа, которые стоят колонкой или обновляются на месте (счётчики, trust, «4 / 5»), набираются ровными цифрами — \`tabular-nums\` на месте числа: в Inter он выравнивает и дефис, поэтому на весь текст его не ставят.

## Сетка и раскладка

Одна шкала ритма на все зазоры, отбивки и поля — сетка Firecrawl с основанием 4px: ${rhythm}px. Ключ — число шагов: \`p-3\` красит ${px(spec.spacing, '3')}, \`gap-6\` — ${px(spec.spacing, '6')}. Каденция: 8px внутри группы, 16px между группами, 24px — поле карточки (16 у компактной), 32px — между секциями. Кнопка: поля ${px(spec.spacing, '4')} по горизонтали.

Размеры вещей — отдельные шкалы (\`size\`, \`height\`, \`width\`): \`p-3\` есть, а \`w-3\` не существует. Брейкпоинты: ${spec.breakpoint.map((b) => `\`${b.name}\` ${b.value}px`).join(', ')}.

## Глубина

Глубину дают тон поверхности и рамка \`line\`, а не тень. Тень \`pop\` носят только всплывающие вещи — диалог, панель, меню. Завесы \`scrim\` (над фото) и \`veil\` (над страницей) — тёмные чернила под альфой.

## Движение

Движение объясняет перемену и никогда не украшает. Наведение и смена краски — \`state\` (${px(spec.duration, 'state')}), появление — \`enter\` (${px(spec.duration, 'enter')}), раскрытие панели — \`reveal\` (${px(spec.duration, 'reveal')}). Нажатие сжимает контрол до \`scale(${spec.pressScale})\`. \`prefers-reduced-motion\` отключает сжатие и петли.

## Формы

${spec.rounded
  .filter((r) => r.note)
  .map((r) => `- \`${r.name}\` ${r.value} — ${r.note.toLowerCase()}.`)
  .join('\n')}

Шкала радиусов — Firecrawl: 8px повседневному контролу, 12px полю, панели и меню, 16px карточке и диалогу. Форма зависит от рода контрола, а не от размера: кнопка — пилюля на всех ступенях, поле — \`md\`, поле внутри коробки — \`sm\`.

## Компоненты

Компонент собирается из рецепта (\`src/shared/design-system/recipes/\`), и рецепт — единственное место, где решены его высота, поля, форма, фокус и disabled. Значения выше в \`components\` прочитаны из самих рецептов.

- **Кнопка.** Высота ${control} (\`md\`), пилюля, надпись ${tokens.fontSize.body} / ${tokens.fontWeight[sets.buttonSize.md.weight.replace('font-', '')]}. Варианты: ${Object.keys(
    sets.buttonVariant,
  )
    .map((v) => `\`${v}\``)
    .join(
      ', ',
    )}. \`primary\` — одно главное действие экрана, остальное — \`secondary\`. \`danger\` — тонированная, а не красная: красная у неё надпись. Размеры \`lg\` (цель касания), \`md\` (подвал диалога), \`sm\` (в карточке), \`xs\` (в строке таблицы). Высоты общие с полями: \`Button size="sm"\` и \`Input size="sm"\` одинаковы.
- **Поле.** Белое, рамка \`line\`, скругление \`md\`. Фокус — свечение \`shadow-focus\` плюс синяя рамка; ошибка — рамка \`danger\` и сообщение рядом (\`FieldError\`), не только цвет.
- **Поверхности.** ${Object.keys(sets.surface)
    .map((s) => `\`${s}\``)
    .join(
      ', ',
    )} — \`surface(variant)\`. Карточка: \`rounded-lg\`, рамка \`line\`, поля ${px(spec.spacing, '6')}.
- **Плашка.** Заливка тона и его \`-deep\` надпись, пилюля, без рамки.

Отключённое — 50% непрозрачности. Фокус клавиатуры у каждой кнопки — 2px обводка \`action-primary\` с отступом 2px.

## Как менять

| Хочу поменять | Где |
| --- | --- |
${spec.howTo.map(([what, where]) => `| ${what} | \`${where}\` |`).join('\n')}

После правки: \`npm run ds:doc\` пересобирает этот файл и обе страницы, \`npm run storybook\` показывает результат вживую.

## Что делать и чего не делать

- Брать значение только из токенов. Нужного нет — сначала спросить, не отвечает ли существующая ступень.
- Называть роль текста (\`type-*\`), а не набирать размер, вес и серый руками.
- Держать одно \`primary\` на экран; всё остальное — \`secondary\` или \`ghost\`.
- Не сообщать состояние одним цветом: рядом иконка или слово.
- Не ставить \`surface-card\` надписью и \`on-fill\` фоном: значение одно, но гейт контраста различает их по имени.
- Не заводить второе имя для краски, которая уже названа: синоним ловит \`tokens.test.ts\`.
- Не класть тень на карточку: глубину даёт рамка.
- Не смешивать пилюлю и прямоугольник у кнопок одного ряда.
`;
}

/* ── Запуск ───────────────────────────────────────────────────────────────── */

function outputs() {
  const spec = buildSpec();
  const md = `${renderFrontmatter(spec)}\n${renderBody(spec)}`;
  return [
    [MD_PATH, md],
    [HTML_PATH, renderPage(spec, md, tokens)],
  ];
}

function main() {
  const check = process.argv.includes('--check');
  let failed = false;
  for (const [path, wanted] of outputs()) {
    const name = path.pathname.split('/').pop();
    let actual = '';
    try {
      actual = readFileSync(path, 'utf8').replace(/\r\n/g, '\n');
    } catch {
      // Нет файла — значит, он разошёлся с токенами так же, как устаревший.
    }
    if (actual === wanted) {
      process.stdout.write(`${name}: соответствует токенам\n`);
    } else if (check) {
      failed = true;
      process.stderr.write(`${name} разошёлся с токенами. Собрать заново: npm run ds:doc\n`);
    } else {
      writeFileSync(path, wanted, 'utf8');
      process.stdout.write(`${name}: обновлён из токенов\n`);
    }
  }
  return failed ? 1 : 0;
}

process.exit(main());
