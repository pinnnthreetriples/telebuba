// Пишет `src/app/styles/tailwind-theme.css` — токены, сказанные языком Tailwind 4.
//
// Tailwind 4 читает тему из CSS (`@theme`), а не из `tailwind.config.ts`. Значения при этом
// остаются в `src/shared/design-system/tokens/*.ts` и только там: этот файл их не хранит,
// а переводит. Правка токена доходит до каждого экрана тем же путём, что и раньше, — через
// `npm run ds:doc`, — а `ds:doc:check` (он в `gates`) падает, пока сгенерированный CSS
// расходится с токенами. Почему не `@config` со старым конфигом, записано в
// `docs/design-system.md`, «Tailwind 4».
//
// Три свойства, ради которых файл устроен так, а не проще:
//
//   1. Шкалы ЗАКРЫТЫ. `--*: initial` снимает все умолчания Tailwind — палитру, ступени
//      шрифта, радиусы, тени, `--spacing`, — а индекс `index.css` не импортирует
//      `tailwindcss/theme.css` вовсе. Класс мимо шкалы (`p-5`, `bg-blue-500`, `text-sm`,
//      `rounded-xl`) не выпускает ничего, как в Tailwind 3.
//   2. `@theme inline`: утилита получает значение, а не `var(--…)`. Это тот CSS, который
//      выпускал Tailwind 3, и у темы не появляется второго входа — переменной на `:root`,
//      которую можно было бы переопределить мимо токенов.
//   3. Стили текста (`type-*`) — слой `components`, как у плагина в Tailwind 3: утилита на
//      том же элементе (`type-small text-danger`) обязана выигрывать.
//   4. `space-x-*`/`space-y-*` — с правилом Tailwind 3 (см. `spaceBlock`): Tailwind 4 его
//      перевернул, и стопки с подписью или скрытым ребёнком сдвинулись бы.
import { readFileSync, writeFileSync } from 'node:fs';

import { colorIds, scale, THEME_NAMESPACES, tokens } from './configScales.mjs';

const OUT = new URL('../src/app/styles/tailwind-theme.css', import.meta.url);

const HEADER = `/* Сгенерировано scripts/tailwind-theme.mjs из src/shared/design-system/tokens —
   руками не править: \`npm run ds:doc\` пересобирает, \`npm run ds:doc:check\` падает на
   расхождении. Решения — в docs/design-system.md, «Tailwind 4». */`;

/** `colors.surface.DEFAULT` → `surface`, `colors.surface.card` → `surface-card`. */
function colorValue(id) {
  for (const [name, value] of Object.entries(tokens.flatColors)) {
    if (typeof value === 'string') {
      if (name === id) return value;
      continue;
    }
    for (const [rung, hex] of Object.entries(value)) {
      if ((rung === 'DEFAULT' ? name : `${name}-${rung}`) === id) return hex;
    }
  }
  throw new Error(`tailwind-theme: нет краски ${id}`);
}

function block(title, lines) {
  return [`  /* ${title} */`, ...lines.map((line) => `  ${line}`)];
}

function vars(namespace, entries) {
  return entries.map(([name, value]) => `--${namespace}-${name}: ${String(value)};`);
}

// `DEFAULT` в шкале — не ступень, а умолчание, которое Tailwind вкладывает в каждую
// утилиту `transition-*` (см. шапку `motion.ts`). В Tailwind 4 у умолчания своё имя.
function withoutDefault(scaleName) {
  return Object.entries(scale(scaleName)).filter(([name]) => name !== 'DEFAULT');
}

function defaultOf(scaleName) {
  const value = scale(scaleName).DEFAULT;
  if (value === undefined) throw new Error(`tailwind-theme: у ${scaleName} нет DEFAULT`);
  return value;
}

function family(names) {
  return names.join(', ');
}

function themeBlock() {
  const lines = [
    '  /* Ни одного умолчания Tailwind: закрытый набор — только то, что ниже. */',
    '  --*: initial;',
    '',
    ...block(
      'Брейкпоинты',
      Object.entries({
        sm: tokens.breakpoint.card,
        md: tokens.breakpoint.split,
        lg: tokens.breakpoint.wide,
      }).map(([name, px]) => `--breakpoint-${name}: ${String(px)}px;`),
    ),
    '',
    ...block(
      'Краски',
      colorIds().map((id) => `--color-${id}: ${colorValue(id)};`),
    ),
    '',
    ...block(
      'Каналы — не краски: только для `rgb(--theme(--channel-*) / α)` в кейфреймах',
      vars('channel', Object.entries(tokens.channel)),
    ),
    '',
    ...block('Шрифты', [
      `--font-sans: ${family(tokens.font.sans)};`,
      `--font-mono: ${family(tokens.font.mono)};`,
      `--default-font-family: ${family(tokens.font.sans)};`,
      `--default-mono-font-family: ${family(tokens.font.mono)};`,
    ]),
    '',
    ...block(
      'Ступени текста: размер вместе со своим интерлиньяжем и трекингом',
      Object.entries(tokens.typeScale).flatMap(([name, step]) => [
        `--text-${name}: ${step.size};`,
        `--text-${name}--line-height: ${step.leading};`,
        `--text-${name}--letter-spacing: ${step.tracking};`,
      ]),
    ),
    '',
    ...block('Веса, интерлиньяж глифа, трекинг кода', [
      ...vars(THEME_NAMESPACES.fontWeight[0], Object.entries(tokens.fontWeight)),
      ...vars(THEME_NAMESPACES.lineHeight[0], Object.entries(tokens.lineHeight)),
      ...vars(THEME_NAMESPACES.letterSpacing[0], Object.entries(tokens.letterSpacing)),
    ]),
    '',
    ...block('Радиусы, тени, слои', [
      ...vars(THEME_NAMESPACES.borderRadius[0], Object.entries(tokens.radius)),
      ...vars(THEME_NAMESPACES.boxShadow[0], Object.entries(tokens.shadow)),
      ...vars(THEME_NAMESPACES.zIndex[0], Object.entries(tokens.layer)),
    ]),
    '',
    ...block('Движение', [
      ...vars(THEME_NAMESPACES.transitionDuration[0], withoutDefault('transitionDuration')),
      `--default-transition-duration: ${defaultOf('transitionDuration')};`,
      ...vars(
        THEME_NAMESPACES.transitionTimingFunction[0],
        withoutDefault('transitionTimingFunction'),
      ),
      `--default-transition-timing-function: ${defaultOf('transitionTimingFunction')};`,
      ...vars(THEME_NAMESPACES.scale[0], Object.entries(tokens.pressScale)),
    ]),
    '',
    ...block(
      'Ритм — в каждом пространстве, которое он питал в Tailwind 3, и ни в одном другом',
      THEME_NAMESPACES.spacing.flatMap((namespace) =>
        vars(namespace, Object.entries(tokens.rhythm)),
      ),
    ),
    '',
    ...block(
      'Размеры — свои шкалы, не ритм',
      ['size', 'height', 'width', 'minWidth', 'maxWidth', 'minHeight', 'maxHeight'].flatMap(
        (name) => vars(THEME_NAMESPACES[name][0], Object.entries(scale(name))),
      ),
    ),
  ];
  return ['@theme inline {', ...lines, '}'].join('\n');
}

// Стиль = ступень + вес + краска. Ссылки через `--theme()`, а не значения: стиль не хранит
// ни одного числа, и стиль, сославшийся на несуществующую ступень, роняет компиляцию.
function rolesBlock() {
  const rules = Object.entries(tokens.typeRole).map(([name, role]) => {
    if (tokens.typeScale[role.size] === undefined || tokens.fontWeight[role.weight] === undefined) {
      throw new Error(`type-${name}: нет ступени «${role.size}» или веса «${role.weight}»`);
    }
    return [
      `  .type-${name} {`,
      `    font-size: --theme(--text-${role.size});`,
      `    line-height: --theme(--text-${role.size}--line-height);`,
      `    letter-spacing: --theme(--text-${role.size}--letter-spacing);`,
      `    font-weight: --theme(--font-weight-${role.weight});`,
      `    color: --theme(--color-${role.ink});`,
      '  }',
    ].join('\n');
  });
  return ['@layer components {', rules.join('\n'), '}'].join('\n');
}

// `space-x-*`/`space-y-*` с правилом Tailwind 3. Tailwind 4 перевернул его: отступ
// получает каждый ребёнок, кроме ПОСЛЕДНЕГО, снизу, — а не каждый, кроме первого, сверху,
// — и с нулевой специфичностью. На подписи (`<label>`, строчный элемент) нижний отступ не
// действует вовсе, скрытый последний ребёнок оставляет хвост, а собственный отступ ребёнка
// начинает складываться с общим. Прежнее правило возвращено целиком: первая строка гасит
// новый нижний отступ (тоже нулевой специфичностью, поэтому собственный класс ребёнка
// по-прежнему выигрывает), вторая — прежнее правило с прежним селектором.
function spaceBlock() {
  const rules = Object.keys(tokens.rhythm).flatMap((name) => {
    const value = `--theme(--space-${name})`;
    return [
      `  :where(.space-y-${name} > *) { margin-block-end: 0; }`,
      `  .space-y-${name} > :not([hidden]) ~ :not([hidden]) { margin-top: ${value}; margin-bottom: 0; }`,
      `  :where(.space-x-${name} > *) { margin-inline-end: 0; }`,
      `  .space-x-${name} > :not([hidden]) ~ :not([hidden]) { margin-left: ${value}; margin-right: 0; }`,
    ];
  });
  return ['@layer utilities {', ...rules, '}'].join('\n');
}

function renderTheme() {
  return `${[HEADER, themeBlock(), rolesBlock(), spaceBlock()].join('\n\n')}\n`;
}

function main() {
  const check = process.argv.includes('--check');
  const wanted = renderTheme();
  let actual = '';
  try {
    actual = readFileSync(OUT, 'utf8').replace(/\r\n/g, '\n');
  } catch {
    // Файла нет — значит, он расходится.
  }
  if (check) {
    if (actual === wanted) {
      process.stdout.write('tailwind-theme.css: соответствует токенам\n');
      return 0;
    }
    process.stderr.write(
      'tailwind-theme.css расходится с src/shared/design-system/tokens. ' +
        'Пересоберите: npm run ds:doc\n',
    );
    return 1;
  }
  if (actual !== wanted) writeFileSync(OUT, wanted, 'utf8');
  process.stdout.write(
    actual === wanted
      ? 'tailwind-theme.css: уже соответствует токенам\n'
      : 'tailwind-theme.css: обновлён из токенов\n',
  );
  return 0;
}

process.exit(main());
