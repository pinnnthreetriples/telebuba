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
// Tailwind получает ПЛОСКИЕ имена, и роль названа в самом имени — это и есть разница с
// первой версией этого файла. Тогда структура ниже была авторитетом на словах, а классы
// оставались прежними: `bg-white`, `text-white`, `bg-primary`, `text-ink`. То есть белый
// цвет карточки и белый цвет надписи на залитой кнопке были ОДНИМ классом, и перекрасить
// одно, не перекрасив другое, было нельзя. Семантический уровень, который нельзя
// применить, — это комментарий, а не уровень.
//
//   background.canvas   → canvas          content.primary   → content-primary
//   background.surface  → surface         content.secondary → content-secondary
//   background.card     → surface-card     content.muted     → content-muted
//   background.inverse  → term            content.subtle    → content-subtle
//   background.scrim    → scrim           action.onPrimary  → on-action
//   background.veil     → veil            (белый на тёмном) → on-inverse
//
//   border.default → line                 action.primary        → action-primary
//   border.strong  → line-strong          action.primaryHover   → action-hover
//   border.subtle  → line-row             action.primaryPressed → action-pressed
//   border.focus   → action-primary
//
//   feedback.info    → info               feedback.warning → warning
//   feedback.success → success            feedback.danger  → danger
//
// Что НЕ переименовано и почему: `canvas`, `line`, `term`, `success`, `warning`, `danger`
// уже были ролями — они называют назначение, а не значение, и второго смысла ни один не
// несёт. Переименование стоило бы 400 правок и не добавило бы ни одной новой
// независимости. Работа была не в приставках, а в трёх краcках, которые делали по две
// работы разом: белый (поверхность и надпись на действии), синий (заливка действия,
// краска ссылки и тёмный синий, читаемый на тоне) и `ink` — имя по материалу, а не по роли.

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
//
// ── Почему имена именно такие ───────────────────────────────────────────────────────
//
// См. таблицу в шапке файла: роль теперь названа в самом классе, а не только в структуре.
// Коротко, что появилось:
//
//   bg-surface-card      белая поверхность, на которую кладут содержимое
//   text-content-primary основные чернила (и -secondary/-muted/-subtle)
//   text-on-action       чернила НА залитом действии
//   text-on-inverse      одна строка на тёмной поверхности (тост, подсказка)
//   bg-action-primary    заливка действия (и -hover/-pressed)
//   text-info-strong     тёмная краска смысла «в работе» на его же тоне
//
// `white` и `black` остаются, но только под альфой над фотографией — единственное место,
// где «белый» это не роль, а край диапазона. Голые `bg-white`/`text-white` банит линтер.
export const flatColors = {
  transparent: palette.transparent,
  current: palette.currentColor,
  white: palette.white,
  black: palette.black,

  canvas: background.canvas,
  surface: {
    // Шаг от белого: строка под курсором, шапка таблицы, вложенный блок.
    DEFAULT: background.surface,
    // Белая поверхность: карточка, диалог, панель, поле ввода. Отдельно от
    // `content['on-action']`, хотя значение то же — в этом весь смысл правки.
    card: palette.white,
    // Приглушённой ступени тут нет умышленно: то же значение носит `line-row`, и оно
    // носится как РАМКА — разделитель строк таблицы. Второе имя для той же краски было бы
    // синонимом, а не ролью, и гейт мёртвых токенов справедливо на него пожаловался.
  },
  scrim: background.scrim,
  veil: background.veil,

  content: {
    primary: content.primary,
    secondary: content.secondary,
    muted: content.muted,
    subtle: content.subtle,
    // `inverse` тут нет умышленно: чернила потока лога на тёмной поверхности — это
    // `term-text`, и второе имя для того же значения было бы синонимом, а не ролью.
    // Одна строка на той же поверхности (тост, подсказка) — это `on-inverse`, и она
    // ЯРЧЕ: её читают один раз, а лог сканируют.
  },
  // Чернила НА залитом действии. Своя ступень, а не `white`: перекрасить надпись кнопки,
  // не перекрасив карточку, — это то, чего раньше было нельзя.
  'on-action': action.onPrimary,
  // Чернила на тёмной поверхности, когда это ОДНА строка, а не поток лога: тост и
  // подсказка. Ярче `term-text` намеренно — тот — основные чернила журнала, который
  // сканируют, а это надпись, которую читают один раз.
  'on-inverse': palette.white,

  line: {
    DEFAULT: border.default,
    strong: border.strong,
    row: border.subtle,
  },
  // Индикатор фокуса, и до этой правки он был ролью НА СЛОВАХ: `border.focus` был
  // объявлен здесь и не доходил ни до одного класса, а восемь контролов рисовали фокус
  // через `outline-action-primary`. То есть перекрасить кнопки означало перекрасить
  // индикатор фокуса — ровно та связь, которую отдельная ступень должна была разорвать.
  // Значение одно (`blue600`), решений два, и теперь оба применимы: `outline-focus`,
  // `border-focus`, `shadow-focus`.
  focus: border.focus,
  action: {
    primary: action.primary,
    // Заливка при наведении на НЕзалитый контрол и подложка выбранной плитки.
    hover: action.primaryHover,
    // Нажатие залитой кнопки: шаг вниз от заливки.
    pressed: action.primaryPressed,
  },
  // Смысл «в работе». Отдельно от `action`, хотя базовое значение то же: тон сообщает
  // состояние, действие приглашает нажать, и перекрасить одно без другого должно быть
  // возможно. Здесь же живёт `strong` — единственный синий, который читается на `tint`.
  info: {
    strong: feedback.info.strong,
    tint: feedback.info.tint,
    line: feedback.info.line,
    // Рамка настолько бледная, что годится и как ЗАЛИВКА разделителя: PipelineCard берёт
    // из одной краски обе работы — рамку карточки и фон сетки, чьи 1px-щели И ЕСТЬ
    // разделители плиток.
    hairline: palette.blue100,
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
