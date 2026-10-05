---
version: alpha
name: Telebuba
description: Дизайн-система Telebuba — операторского дашборда для Telegram. Тёплый серый фон, белые карточки, один синий для действия и тона смысла с подложкой и рамкой. Светлая тема — единственная. Файл собран из src/shared/design-system командой npm run ds:doc; правка руками будет перезаписана.
colors:
  # Действие
  focus: "#0066ff"
  action-primary: "#0066ff"
  action-hover: "#eef4ff"
  action-pressed: "#0057db"
  # Основа
  white: "#ffffff"
  black: "#000000"
  canvas: "#f1efed"
  surface: "#faf9f7"
  surface-card: "#ffffff"
  scrim: "rgb(11 11 12 / 0.55)"
  veil: "rgb(11 11 12 / 0.40)"
  fallback-start: "#cbd7ec"
  fallback-end: "#e6e5e3"
  # Текст
  content-primary: "#0b0b0c"
  content-secondary: "#3a3a3a"
  content-muted: "#63615d"
  content-subtle: "#6e6b66"
  # Чернила на заливке
  on-action: "#ffffff"
  on-action-track: "#5ba3ff"
  on-success: "#ffffff"
  on-warning: "#ffffff"
  on-danger: "#ffffff"
  on-inverse: "#ffffff"
  on-neutral: "#ffffff"
  # Линии
  line: "#e6e5e3"
  line-strong: "#d8d6d2"
  line-row: "#f0eeeb"
  # Смысл
  info-strong: "#0052cc"
  info-tint: "#eef4ff"
  info-line: "#cbd7ec"
  info-hairline: "#e4ecfa"
  success: "#12a150"
  success-deep: "#0b6b37"
  success-press: "#0a5c2f"
  success-tint: "#ddf7e9"
  success-line: "#b8ecce"
  warning: "#9a7b22"
  warning-deep: "#7a5e12"
  warning-press: "#c47d12"
  warning-tint: "#fff0d2"
  warning-line: "#efd79a"
  danger: "#c0473f"
  danger-deep: "#a83a33"
  danger-tint: "#fbecec"
  danger-line: "#f0c9c5"
  # Тёмная поверхность
  term: "#16161a"
  term-thumb: "#2b2b2e"
  term-dim: "#80808c"
  term-text: "#c9c9d3"
  term-link: "#6ea8fe"
  term-error: "#e5736b"
  term-success: "#7be0a6"
  term-warning: "#ffd27f"
fontFamily:
  sans: "Inter, system-ui, sans-serif"
  mono: "\"JetBrains Mono\", ui-monospace, monospace"
typography:
  h1:
    fontFamily: "Inter"
    fontSize: 24px
    fontWeight: 500
    lineHeight: 32px
    letterSpacing: -0.019em
    color: "{colors.content-primary}"
  h2:
    fontFamily: "Inter"
    fontSize: 20px
    fontWeight: 500
    lineHeight: 28px
    letterSpacing: -0.017em
    color: "{colors.content-primary}"
  h3:
    fontFamily: "Inter"
    fontSize: 16px
    fontWeight: 500
    lineHeight: 24px
    letterSpacing: -0.011em
    color: "{colors.content-primary}"
  body:
    fontFamily: "Inter"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 20px
    letterSpacing: -0.006em
    color: "{colors.content-secondary}"
  body-medium:
    fontFamily: "Inter"
    fontSize: 14px
    fontWeight: 500
    lineHeight: 20px
    letterSpacing: -0.006em
    color: "{colors.content-primary}"
  small:
    fontFamily: "Inter"
    fontSize: 12px
    fontWeight: 400
    lineHeight: 16px
    letterSpacing: 0em
    color: "{colors.content-subtle}"
  small-medium:
    fontFamily: "Inter"
    fontSize: 12px
    fontWeight: 500
    lineHeight: 16px
    letterSpacing: 0em
    color: "{colors.content-subtle}"
spacing:
  px: 1px
  hair: 2px
  xs: 4px
  tight: 6px
  sm: 8px
  md: 12px
  lg: 16px
  xl: 20px
  2xl: 24px
  page: 32px
  empty: 64px
rounded:
  none: 0px
  sm: 6px
  md: 8px
  lg: 11px
  card: 16px
  full: 9999px
shadows:
  pop: "0 10px 30px rgb(11 11 12 / 0.12)"
  ring: "0 0 0 1px rgb(11 11 12 / 0.07)"
  thumb: "0 1px 3px rgb(0 0 0 / 0.3)"
  focus: "0 0 0 3px rgb(0 102 255 / 0.12)"
  seg: "0 1px 2px 0 rgb(0 0 0 / 0.05)"
  pill: "0 1px 2px rgb(0 102 255 / 0.3)"
height:
  compact: 28px
  field: 32px
  control: 36px
  touch: 44px
duration:
  state: 150ms
  enter: 250ms
  swap: 340ms
  reveal: 420ms
  spin: 800ms
  roll: 900ms
  pulse: 1200ms
  stagger: 90ms
easing:
  out: "cubic-bezier(.16,1,.3,1)"
  spring: "cubic-bezier(.34,1.45,.6,1)"
  linear: "linear"
  breathe: "cubic-bezier(.4,0,.6,1)"
breakpoints:
  table: 880px
  wide: 1024px
  card: 640px
  split: 768px
components:
  button-primary:
    backgroundColor: "{colors.action-primary}"
    textColor: "{colors.on-action}"
    hoverBackgroundColor: "{colors.action-pressed}"
    height: "{height.control}"
    paddingX: "{spacing.2xl}"
    fontSize: "{fontSize.body}"
    rounded: "{rounded.full}"
    fontWeight: 500
  button-neutral:
    backgroundColor: "{colors.content-primary}"
    textColor: "{colors.on-neutral}"
    hoverBackgroundColor: "{colors.content-primary}"
    height: "{height.control}"
    paddingX: "{spacing.2xl}"
    fontSize: "{fontSize.body}"
    rounded: "{rounded.full}"
    fontWeight: 500
  button-secondary:
    borderWidth: 1px
    borderColor: "{colors.line}"
    backgroundColor: "{colors.surface-card}"
    textColor: "{colors.content-primary}"
    hoverBorderColor: "{colors.line-strong}"
    height: "{height.control}"
    paddingX: "{spacing.2xl}"
    fontSize: "{fontSize.body}"
    rounded: "{rounded.full}"
    fontWeight: 500
  button-danger:
    borderWidth: 1px
    borderColor: "{colors.danger-line}"
    backgroundColor: "{colors.danger-tint}"
    textColor: "{colors.danger-deep}"
    hoverBorderColor: "{colors.danger}"
    height: "{height.control}"
    paddingX: "{spacing.2xl}"
    fontSize: "{fontSize.body}"
    rounded: "{rounded.full}"
    fontWeight: 500
  button-ghost:
    textColor: "{colors.content-muted}"
    hoverBackgroundColor: "{colors.canvas}"
    hoverTextColor: "{colors.content-primary}"
    height: "{height.control}"
    paddingX: "{spacing.2xl}"
    fontSize: "{fontSize.body}"
    rounded: "{rounded.full}"
    fontWeight: 500
  button-dashed:
    borderWidth: 1px
    borderStyle: "dashed"
    borderColor: "{colors.info-line}"
    backgroundColor: "{colors.surface-card}"
    textColor: "{colors.info-strong}"
    hoverBorderColor: "{colors.action-primary}"
    hoverBackgroundColor: "{colors.action-hover}"
    height: "{height.control}"
    paddingX: "{spacing.2xl}"
    fontSize: "{fontSize.body}"
    rounded: "{rounded.full}"
    fontWeight: 500
  button-dashed-muted:
    borderWidth: 1px
    borderStyle: "dashed"
    borderColor: "{colors.line-strong}"
    backgroundColor: "{colors.surface-card}"
    textColor: "{colors.content-muted}"
    hoverBorderColor: "{colors.action-primary}"
    hoverTextColor: "{colors.action-primary}"
    height: "{height.control}"
    paddingX: "{spacing.2xl}"
    fontSize: "{fontSize.body}"
    rounded: "{rounded.full}"
    fontWeight: 500
  button-lg:
    height: "{height.touch}"
    paddingX: "{spacing.2xl}"
    fontSize: "{fontSize.body}"
    rounded: "{rounded.full}"
    fontWeight: 500
  button-sm:
    height: "{height.field}"
    paddingX: "{spacing.xl}"
    fontSize: "{fontSize.body}"
    rounded: "{rounded.full}"
    fontWeight: 500
  button-xs:
    height: "{height.compact}"
    paddingX: "{spacing.md}"
    fontSize: "{fontSize.body}"
    rounded: "{rounded.full}"
    fontWeight: 500
  input:
    borderWidth: 1px
    backgroundColor: "{colors.surface-card}"
    borderColor: "{colors.line}"
    height: "{height.control}"
    paddingX: "{spacing.md}"
    fontSize: "{fontSize.body}"
    rounded: "{rounded.lg}"
  input-sm:
    borderWidth: 1px
    backgroundColor: "{colors.surface-card}"
    borderColor: "{colors.line}"
    height: "{height.field}"
    paddingX: "{spacing.md}"
    fontSize: "{fontSize.body}"
    rounded: "{rounded.lg}"
  input-xs:
    borderWidth: 1px
    backgroundColor: "{colors.surface-card}"
    borderColor: "{colors.line}"
    height: "{height.compact}"
    paddingX: "{spacing.md}"
    fontSize: "{fontSize.body}"
    rounded: "{rounded.md}"
  input-flat:
    borderWidth: 1px
    backgroundColor: "{colors.canvas}"
    borderColor: "{colors.line}"
    height: "{height.control}"
    paddingX: "{spacing.md}"
    fontSize: "{fontSize.body}"
    rounded: "{rounded.lg}"
  card:
    rounded: "{rounded.card}"
    borderWidth: 1px
    borderColor: "{colors.line}"
    backgroundColor: "{colors.surface-card}"
    paddingX: "{spacing.xl}"
    paddingY: "{spacing.xl}"
  dialog:
    rounded: "{rounded.card}"
    backgroundColor: "{colors.surface-card}"
    shadow: "{shadows.pop}"
  panel:
    rounded: "{rounded.lg}"
    borderWidth: 1px
    borderColor: "{colors.line}"
    backgroundColor: "{colors.surface-card}"
    shadow: "{shadows.pop}"
  inset:
    rounded: "{rounded.lg}"
    backgroundColor: "{colors.canvas}"
  inverse:
    rounded: "{rounded.lg}"
    backgroundColor: "{colors.term}"
    textColor: "{colors.term-text}"
  badge-neutral:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.content-muted}"
    rounded: "{rounded.full}"
    fontWeight: 500
    paddingX: "{spacing.sm}"
    paddingY: "{spacing.hair}"
    fontSize: "{fontSize.small}"
  badge-info:
    backgroundColor: "{colors.info-tint}"
    textColor: "{colors.info-strong}"
    rounded: "{rounded.full}"
    fontWeight: 500
    paddingX: "{spacing.sm}"
    paddingY: "{spacing.hair}"
    fontSize: "{fontSize.small}"
  badge-success:
    backgroundColor: "{colors.success-tint}"
    textColor: "{colors.success-deep}"
    rounded: "{rounded.full}"
    fontWeight: 500
    paddingX: "{spacing.sm}"
    paddingY: "{spacing.hair}"
    fontSize: "{fontSize.small}"
  badge-warning:
    backgroundColor: "{colors.warning-tint}"
    textColor: "{colors.warning-deep}"
    rounded: "{rounded.full}"
    fontWeight: 500
    paddingX: "{spacing.sm}"
    paddingY: "{spacing.hair}"
    fontSize: "{fontSize.small}"
  badge-danger:
    backgroundColor: "{colors.danger-tint}"
    textColor: "{colors.danger-deep}"
    rounded: "{rounded.full}"
    fontWeight: 500
    paddingX: "{spacing.sm}"
    paddingY: "{spacing.hair}"
    fontSize: "{fontSize.small}"
---

# Telebuba

## Обзор

Telebuba — операторский дашборд: аккаунты, прокси, прогрев, нейрокомментинг, нейрошиллинг. Интерфейс плотный и спокойный: белые карточки лежат на тёплом сером фоне, иерархию дают тон поверхности и рамка, а цвет несёт смысл. Синий `action-primary` — это действие; зелёный, янтарный и красный — исход и состояние. Тёмная поверхность `term` одна — журнал и подсказки.

Это закрытый набор. Каждое значение интерфейса берётся отсюда: сырой hex или произвольное `[7px]` в коде — ошибка линтера, а ступень, которую никто не носит, — ошибка гейта `ds:dead`. Значение меняют в `src/shared/design-system/tokens/`, и оно меняется везде.

## Цвета

Уровень 1 — палитра (`primitives.ts`): сырые краски, каждая записана один раз. Уровень 2 — назначение (`semantic.ts`): имена из этого файла ссылаются на палитру. Класс всегда называет назначение (`bg-surface-card`, `text-on-action`), а не краску, поэтому белая карточка и белая надпись на кнопке перекрашиваются независимо.

- **Основа.** `canvas` — под предметом и всё заполняемое, `surface-card` — сам предмет (карточка, диалог, поле), `surface` — шаг от белого внутри предмета.
- **Текст.** `content-primary` → `secondary` → `muted` → `subtle`. Последние два стоят на пороге AA: светлее не бывает.
- **Действие.** `action-primary` заливает главную кнопку, `action-pressed` — её нажатие, `action-hover` — наведение на незалитое. `focus` — тот же синий, но отдельное решение.
- **Смысл.** У каждого тона: основной (текст, иконка), `-tint` (подложка), `-line` (рамка подложки), `-deep` (текст на подложке, когда основной не проходит по контрасту).
- **Чернила на заливке.** `on-action`, `on-success`, `on-danger`, `on-neutral` — надпись на залитом. Каждая своим именем, чтобы перекрасить одно, не задев другое.

## Типографика

Inter набирает весь интерфейс, JetBrains Mono — код, идентификаторы и журнал. Пять ступеней: `small` 12px/16px, `body` 14px/20px, `h3` 16px/24px, `h2` 20px/28px, `h1` 24px/32px. Каждый размер — один уровень иерархии, интерлиньяж кратен 4px, трекинг оптический (Inter Dynamic Metrics) и входит в ступень.

Весов два: `font-normal` 400, `font-medium` 500. 400 — то, что читают, 500 — то, что называют. Иерархию держит размер, а не жирность.

Страница называет **стиль**: `type-h1`, `type-h2`, `type-h3`, `type-body`, `type-body-medium`, `type-small`, `type-small-medium`. Стиль несёт ступень, вес и краску по умолчанию; другой цвет пишется утилитой поверх: `type-small text-danger`. Цифры везде ровные (`tabular-nums` на `body`).

## Сетка и раскладка

Одна шкала ритма на все зазоры, отбивки и поля: 2, 4, 6, 8, 12, 16, 20, 24, 32, 64px. Имена называют работу: `tight` — части одной вещи, `sm` — внутри контрола, `md` — строки внутри карточки, `lg` — блоки карточки, `2xl` — поля контрола, `page` — поля страницы.

Размеры вещей — отдельные шкалы (`size`, `height`, `width`): `p-md` красит 12px, а `w-md` не существует. Брейкпоинты: `table` 880px, `wide` 1024px, `card` 640px, `split` 768px.

## Глубина

Глубину дают тон поверхности и рамка `line`, а не тень. Тень `pop` носят только всплывающие вещи — диалог, панель, меню. Завесы `scrim` (над фото) и `veil` (над страницей) — тёмные чернила под альфой.

## Движение

Движение объясняет перемену и никогда не украшает. Наведение и смена краски — `state` (150ms), появление — `enter` (250ms), раскрытие панели — `reveal` (420ms). Нажатие сжимает контрол до `scale(0.96)`. `prefers-reduced-motion` отключает сжатие и петли.

## Формы

- `none` 0px — без скругления.
- `sm` 6px — контрол внутри другой коробки.
- `md` 8px — самостоятельный контрол, поле внутри коробки.
- `lg` 11px — панель в карточке, поле ввода.
- `card` 16px — карточка и диалог.
- `full` 9999px — кнопка, плашка, аватар.

Форма зависит от рода контрола, а не от размера: кнопка — пилюля на всех ступенях, поле — `lg`, поле внутри коробки — `md`.

## Компоненты

Компонент собирается из рецепта (`src/shared/design-system/recipes/`), и рецепт — единственное место, где решены его высота, поля, форма, фокус и disabled. Значения выше в `components` прочитаны из самих рецептов.

- **Кнопка.** Высота 36px (`md`), пилюля, надпись 14px / 500. Варианты: `primary`, `neutral`, `secondary`, `danger`, `ghost`, `dashed`, `dashedMuted`. `primary` — одно главное действие экрана, остальное — `secondary`. `danger` — тонированная, а не красная: красная у неё надпись. Размеры `lg` (цель касания), `md` (подвал диалога), `sm` (в карточке), `xs` (в строке таблицы). Высоты общие с полями: `Button size="sm"` и `Input size="sm"` одинаковы.
- **Поле.** Белое, рамка `line`, скругление `lg`. Фокус — свечение `shadow-focus` плюс синяя рамка; ошибка — рамка `danger` и сообщение рядом (`FieldError`), не только цвет.
- **Поверхности.** `card`, `dialog`, `panel`, `inset`, `inverse` — `surface(variant)`. Карточка: `rounded-card`, рамка `line`, поля 20px.
- **Плашка.** Заливка тона и его `-deep` надпись, пилюля, без рамки.

Отключённое — 50% непрозрачности. Фокус клавиатуры у каждой кнопки — 2px обводка `focus` с отступом 2px.

## Как менять

| Хочу поменять | Где |
| --- | --- |
| Цвет везде | `src/shared/design-system/tokens/primitives.ts → palette` |
| Что считается «действием», «ошибкой» | `src/shared/design-system/tokens/semantic.ts` |
| Сетку отступов | `src/shared/design-system/tokens/spacing.ts → rhythm` |
| Высоту кнопок и полей | `src/shared/design-system/tokens/spacing.ts → height` |
| Шрифты и роли текста | `src/shared/design-system/tokens/typography.ts` |
| Скругления и тени | `src/shared/design-system/tokens/primitives.ts → radius, shadow` |
| Форму и поля всех контролов | `src/shared/design-system/recipes/controls.ts` |
| Заливку вариантов кнопки | `src/shared/ui/Button.tsx → VARIANT` |
| Карточки, панели, диалоги | `src/shared/design-system/recipes/surfaces.ts → SURFACE` |
| Тона плашек и уведомлений | `src/shared/design-system/recipes/feedback.ts → TONE` |

После правки: `npm run ds:doc` пересобирает этот файл и обе страницы, `npm run storybook` показывает результат вживую.

## Что делать и чего не делать

- Брать значение только из токенов. Нужного нет — сначала спросить, не отвечает ли существующая ступень.
- Называть роль текста (`type-*`), а не набирать размер, вес и серый руками.
- Держать одно `primary` на экран; всё остальное — `secondary` или `ghost`.
- Не сообщать состояние одним цветом: рядом иконка или слово.
- Не ставить `surface-card` надписью и `on-action` фоном: значение одно, работы разные.
- Не класть тень на карточку: глубину даёт рамка.
- Не смешивать пилюлю и прямоугольник у кнопок одного ряда.
