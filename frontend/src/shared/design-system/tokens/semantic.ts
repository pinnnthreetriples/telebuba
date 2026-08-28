// Уровень 2. Назначение краски — и ни одного значения: каждая ступень ссылается на
// `primitives.ts`.
//
// Две ступени могут указывать на одно значение, и значение при этом не копируется.
// Обратное правило важнее: РАЗНЫЕ смыслы не сливаются от того, что сегодня совпали
// цветом. `border.focus` и `action.primary` — один синий и два решения; перекрасить
// действие, не перекрасив фокус, должно быть возможно, не переписывая сайты.
//
// ── Как это доходит до классов ─────────────────────────────────────────────────────
//
// Tailwind получает ПЛОСКИЕ имена, и они не переименовываются в `background-canvas`:
// класс — это то, что набрано в 700 местах приложения, а `bg-canvas` уже говорит ровно
// то, что говорит `background.canvas`. Переименование стоило бы 700 правок и не сказало
// бы ничего нового. Структура ниже — авторитет, `flatColors` — её проекция на классы, а
// таблица соответствий одна и лежит здесь же, а не в чьей-то голове:
//
//   background.canvas   → canvas          content.primary   → ink
//   background.surface  → surface         content.secondary → ink-body
//   background.muted    → line-row        content.muted     → ink-muted
//   background.inverse  → term            content.subtle    → ink-subtle
//   background.scrim    → scrim           content.inverse   → term-text
//   background.veil     → veil            content.disabled  → ink-subtle
//
//   border.default → line                 action.primary        → primary
//   border.strong  → line-strong          action.primaryHover   → primary-tint
//   border.subtle  → line-row             action.primaryPressed → primary-press
//   border.focus   → primary              action.onPrimary      → white
//
//   feedback.success → success            feedback.warning → warning
//   feedback.danger  → danger             feedback.info    → primary
//
// Каждая семантическая группа, кроме `feedback`, разворачивается в один плоский ключ;
// `feedback` — в рампу из пяти рунгов, потому что тон носит и текст, и подложку, и рамку.
import { palette } from './primitives';

export const background = {
  // Земля, на которой стоит страница, и заливка всего, что заполняется НА карточке:
  // дорожка прогресса, чип, счётчик. Две работы, одна краска — они были двумя токенами
  // в трёх единицах друг от друга, а это меньше порога, на котором плоская область
  // читается другим цветом.
  canvas: palette.warmGrey100,
  // Один шаг от белого: строка под курсором и коробка, которая приглашает в себя.
  surface: palette.warmGrey050,
  // Приглушённая подложка — разделитель строк таблицы, он же фон вложенного блока.
  muted: palette.warmGrey200,
  // Тёмная поверхность: терминал логов и подсказки, которые делят с ним чернила.
  inverse: palette.ink900,
  // Завеса над ОДНОЙ фотографией: белый контроль поверх должен брать 4.5:1.
  scrim: palette.scrim55,
  // Завеса над всей страницей: на ней ничего не пишут, она приглушает, а не контрастит.
  veil: palette.veil40,
} as const;

export const content = {
  primary: palette.warmGrey900,
  secondary: palette.warmGrey800,
  // `muted` и `subtle` — два серых, которыми написан мелкий текст, и оба стоят на полу
  // AA, а не там, где смотрелись лучше: рампа сжата нарочно, потому что альтернатива —
  // ступень, про которую дизайн-система знает, что её нельзя прочесть.
  muted: palette.warmGrey700,
  subtle: palette.warmGrey600,
  // Чернила на тёмной поверхности.
  inverse: palette.inkGrey200,
  // Отдельная ступень, а не `subtle`, хотя значение то же: недоступный контрол и мелкая
  // подпись — разные решения, и первое обычно меняют вместе с непрозрачностью, а не с
  // серым. Слитые, они меняются вместе поневоле.
  disabled: palette.warmGrey600,
} as const;

export const border = {
  default: palette.warmGrey300,
  strong: palette.warmGrey400,
  subtle: palette.warmGrey200,
  // Тот же синий, что у действия, и отдельной ступенью именно поэтому.
  focus: palette.blue600,
} as const;

export const action = {
  primary: palette.blue600,
  // Заливка при наведении на НЕзалитый контрол, она же подложка выбранной плитки.
  primaryHover: palette.blue050,
  // Нажатие залитой кнопки: шаг ВНИЗ от заливки.
  primaryPressed: palette.blue700,
  // Чернила на залитом действии.
  onPrimary: palette.white,
  // Недоступное действие. Значение — та же приглушённая краска: непрозрачность добавляет
  // сам контрол, и ступень описывает краску, а не итог.
  disabled: palette.warmGrey600,
} as const;

// Смысл, который сообщает интерфейс. У каждого тона одни и те же пять работ, чтобы
// компоненту никогда не приходилось изобретать оттенок: `base` — текст и иконка,
// `strong` — самая тёмная ступень для заголовка НА тонированной подложке, `pressed` —
// нажатие залитой кнопки этого тона, `tint` — сама подложка, `line` — её рамка.
//
// `strong` существует не для красоты: `success.base` на `success.tint` даёт 2.97:1,
// `warning.base` — 4.0:1 на белом, `danger.base` — 4.34:1 на своём тоне, а каждая плашка
// «удалён» в приложении набрана мелким. Пол AA — 4.5:1, и `strong` его берёт.
export const feedback = {
  info: {
    base: palette.blue600,
    strong: palette.blue800,
    pressed: palette.blue700,
    tint: palette.blue050,
    line: palette.blue200,
  },
  success: {
    base: palette.green500,
    strong: palette.green700,
    pressed: palette.green800,
    tint: palette.green050,
    line: palette.green200,
  },
  warning: {
    base: palette.amber600,
    strong: palette.amber700,
    // Единственный тон, у которого `pressed` ярче базового: янтарный вниз уходит в
    // коричневый, и нажатие читается только вверх. Носитель один — заливка счётчика.
    pressed: palette.amber500,
    tint: palette.amber050,
    line: palette.amber200,
  },
  danger: {
    base: palette.red500,
    strong: palette.red600,
    pressed: palette.red600,
    tint: palette.red050,
    line: palette.red200,
  },
} as const;

// Чернила тёмной поверхности: у терминала своя рампа, потому что светлотемные краски на
// #16161a не читаются, а не потому, что кому-то захотелось второй набор.
export const inverse = {
  surface: palette.ink900,
  // Ползунок прокрутки на этой поверхности.
  thumb: palette.ink800,
  dim: palette.inkGrey500,
  text: palette.inkGrey200,
  link: palette.inkBlue300,
  error: palette.inkRed300,
  success: palette.inkGreen300,
  warning: palette.inkAmber300,
} as const;

// Проекция семантики на плоские имена, которые набирает класс. Всё, что ниже, — ссылки:
// ни одного значения, только пути в структуры выше.
export const flatColors = {
  transparent: palette.transparent,
  current: palette.currentColor,
  white: palette.white,
  black: palette.black,

  canvas: background.canvas,
  surface: background.surface,
  scrim: background.scrim,
  veil: background.veil,

  ink: {
    DEFAULT: content.primary,
    body: content.secondary,
    muted: content.muted,
    subtle: content.subtle,
  },
  line: {
    DEFAULT: border.default,
    strong: border.strong,
    row: border.subtle,
  },
  primary: {
    DEFAULT: action.primary,
    press: action.primaryPressed,
    tint: action.primaryHover,
    line: feedback.info.line,
    // Рамка настолько бледная, что годится и как ЗАЛИВКА разделителя: neurocomment
    // PipelineCard берёт из одной краски обе работы — рамку карточки и фон сетки, чьи
    // 1px-щели И ЕСТЬ разделители плиток.
    hairline: palette.blue100,
    deep: feedback.info.strong,
  },
  success: {
    DEFAULT: feedback.success.base,
    deep: feedback.success.strong,
    press: feedback.success.pressed,
    tint: feedback.success.tint,
    line: feedback.success.line,
  },
  warning: {
    DEFAULT: feedback.warning.base,
    deep: feedback.warning.strong,
    strong: feedback.warning.pressed,
    tint: feedback.warning.tint,
    line: feedback.warning.line,
  },
  danger: {
    DEFAULT: feedback.danger.base,
    deep: feedback.danger.strong,
    tint: feedback.danger.tint,
    line: feedback.danger.line,
  },
  term: {
    DEFAULT: inverse.surface,
    thumb: inverse.thumb,
    dim: inverse.dim,
    text: inverse.text,
    link: inverse.link,
    error: inverse.error,
    success: inverse.success,
    warning: inverse.warning,
  },
} as const;
