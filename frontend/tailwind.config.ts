import type { Config } from 'tailwindcss';
import plugin from 'tailwindcss/plugin';

import {
  channel,
  duration,
  easing,
  flatColors,
  font,
  fontSize,
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
  shadow,
  size,
  typeRole,
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
  // каталог, всё равно мёртвая.
  content: ['./index.html', './src/**/*.{ts,tsx}', './catalog/**/*.{html,ts,tsx}'],
  theme: {
    colors: flatColors,
    // Не шкала утилит: `channel` не выпускает ни одного класса и существует только для
    // `theme()` в кейфреймах, которым нужна краска с альфой. См. заметку в `primitives.ts`.
    channel,
    fontFamily: font,
    fontSize,
    typeRole,
    lineHeight,
    letterSpacing,
    borderRadius: radius,
    boxShadow: shadow,
    transitionDuration: duration,
    transitionTimingFunction: easing,
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
    // По одной утилите на роль, в слой `components`, чтобы утилита на том же элементе всё
    // ещё выигрывала: `type-caption text-danger` — подпись в цвете ошибки, а
    // `type-card-title font-bold` — заголовок, за который кому-то ещё придётся спорить.
    // Этот порядок и есть причина, по которой здесь плагин, а не рецепт на `@apply`.
    plugin(({ addComponents, theme }) => {
      type Role = { size: string; weight: string; ink: string; tracking?: string; caps?: string };
      const roles = theme('typeRole') as Record<string, Role>;
      addComponents(
        Object.fromEntries(
          Object.entries(roles).map(([name, role]) => [
            `.type-${name}`,
            {
              fontSize: theme(`fontSize.${role.size}`) as string,
              fontWeight: role.weight,
              // `content-primary` — как это пишет утилита; палитра рампу вкладывает,
              // поэтому дефис на пути превращается в точку. Спецслучая для «краски без
              // рунга» больше нет: у `content` каждая ступень названа, и `ink` как
              // отдельное имя ушло вместе с переездом на роли.
              color: theme(`colors.${role.ink.replace('-', '.')}`) as string,
              ...(role.tracking === undefined ? {} : { letterSpacing: role.tracking }),
              ...(role.caps === undefined ? {} : { textTransform: role.caps }),
            },
          ]),
        ),
      );
    }),
  ],
} satisfies Config;
