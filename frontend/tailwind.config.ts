import type { Config } from 'tailwindcss';
import plugin from 'tailwindcss/plugin';

import {
  breakpoint,
  channel,
  duration,
  easing,
  flatColors,
  font,
  fontWeight,
  height,
  layer,
  letterSpacing,
  lineHeight,
  maxHeight,
  maxWidth,
  minHeight,
  minWidth,
  radius,
  rhythm,
  pressScale,
  shadow,
  size,
  typeRole,
  typeScale,
  width,
} from './src/shared/design-system/tokens';

// Подключение токенов к Tailwind — и ничего кроме.
//
// Значений здесь нет: они в `src/shared/design-system/tokens/`, откуда их читают и этот
// файл, и `src/shared/lib/cn.ts`, и — текстом — гейты в `scripts/`. Почему не наоборот
// (значения в конфиге, остальные импортируют его): этот файл импортирует
// `tailwindcss/plugin`, а `cn.ts` — код приложения, и импорт конфига затащил бы плагин
// Tailwind в браузерный бандл.
//
// Обоснования — почему ступени именно такие, что во что слито и что оставлено отдельно —
// в `docs/design-system.md`. Здесь их нет умышленно: пока они лежали в конфиге, файл был
// 731 строкой, из которых 350 — проза, и найти в нём саму шкалу было труднее, чем прочесть
// про неё.
//
// Каждая шкала стоит в КОРНЕ `theme`, а не в `extend`, и это не стиль: `extend` оставляет
// шкалу Tailwind доступной рядом со своей, поэтому `text-sm` (14px) мог приехать к
// `text-lead` (13px), а `bg-blue-500` — к `bg-primary`. Единственное, что стояло между
// двумя палитрами, — регулярка в правиле ESLint, читающая только `src` и только вне
// тестов. Закрытый набор, который держит grep, — не закрытый набор.
export default {
  // `catalog/` — живой каталог примитивов (отдельная точка входа Vite, вне бандла
  // приложения). Он носит те же классы, что и `src`, поэтому попадает в content; но
  // `ds:dead` читает только `src`, и это правильно: ступень, которую носит один
  // каталог, всё равно мёртвая. Storybook также использует классы в `stories/`.
  content: [
    './index.html',
    './src/**/*.{ts,tsx}',
    './catalog/**/*.{html,ts,tsx}',
    './stories/**/*.{ts,tsx}',
  ],
  theme: {
    // Шкала брейкпоинтов закрыта, как и все остальные: три ступени, которые приложение
    // носит (16 сайтов `sm:`, 22 `md:`, 27 `lg:`), и ни одной, которую не носит. Числа
    // приходят из `breakpoint` — той же основы, что читает `useWideViewport.ts`, потому
    // что «таблица или карточки» решается и в CSS, и в JavaScript. Умолчания Tailwind
    // (`xl`, `2xl`) заменены, а не расширены: правило ESLint называет три существующие.
    screens: {
      sm: `${String(breakpoint.card)}px`,
      md: `${String(breakpoint.split)}px`,
      lg: `${String(breakpoint.wide)}px`,
    },
    colors: flatColors,
    // Не шкала утилит: `channel` не выпускает ни одного класса и существует только для
    // `theme()` в кейфреймах, которым нужна краска с альфой. См. заметку в `primitives.ts`.
    channel,
    fontFamily: font,
    // Ступень — размер вместе со своим интерлиньяжем и трекингом: `text-body` ставит все три.
    // Пара собирается здесь, а не в токенах, потому что форма `[размер, { … }]` — формат
    // Tailwind, а не решение дизайна.
    fontSize: Object.fromEntries(
      Object.entries(typeScale).map(([name, step]) => [
        name,
        [step.size, { lineHeight: step.leading, letterSpacing: step.tracking }],
      ]),
    ),
    fontWeight,
    // Не шкала утилит, как и `channel`: ступени целиком для `theme()` в `index.css` и для
    // плагина стилей ниже.
    typeScale,
    typeRole,
    lineHeight,
    letterSpacing,
    borderRadius: radius,
    boxShadow: shadow,
    transitionDuration: duration,
    transitionTimingFunction: easing,
    scale: pressScale,
    zIndex: layer,
    // Ритм заменяет числовую шкалу Tailwind: зазор и отбивка — одно измерение с двух
    // сторон, и держать их разными шкалами — это как `gap-md` (10px) оказался рядом с
    // `px-3` (12px) в одной строке. Заменить, а не расширить, стало безопасно только
    // после того, как размеры компонентов уехали в свои шкалы ниже: `spacing` питает и
    // `w-*`, а 34px аватара — не ступень ритма.
    spacing: rhythm,
    size,
    height,
    width,
    minWidth,
    maxWidth,
    minHeight,
    maxHeight,
  },
  plugins: [
    // По одной утилите на стиль, в слой `components`, чтобы утилита на том же элементе всё
    // ещё выигрывала: `type-small text-danger` — подпись в цвете ошибки. Этот порядок и
    // есть причина, по которой здесь плагин, а не рецепт на `@apply`.
    plugin(({ addComponents, theme }) => {
      type Step = { size: string; leading: string; tracking: string };
      type Role = { size: string; weight: string; ink: string };
      const steps = theme('typeScale') as Record<string, Step>;
      const weights = theme('fontWeight') as Record<string, string>;
      const roles = theme('typeRole') as Record<string, Role>;
      addComponents(
        Object.fromEntries(
          Object.entries(roles).map(([name, role]) => {
            const step = steps[role.size];
            const weight = weights[role.weight];
            // Стиль, сославшийся на несуществующую ступень или вес, должен ронять сборку,
            // а не выпускать класс без размера.
            if (step === undefined || weight === undefined) {
              throw new Error(`type-${name}: нет ступени «${role.size}» или веса «${role.weight}»`);
            }
            return [
              `.type-${name}`,
              {
                fontSize: step.size,
                lineHeight: step.leading,
                letterSpacing: step.tracking,
                fontWeight: weight,
                // `content-primary` — как это пишет утилита; палитра рампу вкладывает,
                // поэтому дефис на пути превращается в точку.
                color: theme(`colors.${role.ink.replace('-', '.')}`) as string,
              },
            ];
          }),
        ),
      );
    }),
  ],
} satisfies Config;
