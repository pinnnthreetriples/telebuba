// Уровень 2. Назначение краски — и ни одного значения: каждая ступень ссылается на
// `primitives.ts`.
//
// ── Один цвет — одно имя ───────────────────────────────────────────────────────────
//
// Две ступени с одним значением — это синоним, а не роль. Прежняя редакция этого файла
// держала обратное правило («разные смыслы не сливаются оттого, что совпали цветом») и
// платила за него восемью именами белого, двумя — синего действия и двумя — его подложки.
// Ни одно из них не перекрашивалось отдельно: второй темы нет, а «чтобы когда-нибудь
// можно было» — обещание, а не решение. Когда смысл действительно разойдётся, имя
// появится вместе с новым значением.
//
// Исключение одно, и оно держится гейтом, а не доводом: `surface-card` и `on-fill` — оба
// #ffffff. `contrast.test.ts` узнаёт пару «краска на подложке» по ИМЕНИ и пару с одинаковым
// именем не меряет (иначе `text-action-primary hover:bg-action-primary` давал бы 1:1 на
// пустом месте). Одно имя на белую карточку и белую надпись сделало бы белое на белом —
// самую дорогую ошибку контраста — невидимой гейту. `tokens.test.ts` проверяет, что других
// совпадений нет.
//
// ── Как это доходит до классов ─────────────────────────────────────────────────────
//
// Tailwind получает ПЛОСКИЕ имена, и роль названа в самом имени:
//
//   background.canvas  → canvas       content.primary   → content-primary
//   background.surface → surface      content.secondary → content-secondary
//   background.card    → surface-card content.muted     → content-muted
//   background.scrim   → scrim        content.subtle    → content-subtle
//   background.veil    → veil         content.onFill    → on-fill
//
//   border.default → line             action.primary        → action-primary
//   border.strong  → line-strong      action.primaryPressed → action-pressed
//                                     action.onPrimaryTrack → on-action-track
//
//   feedback.info    → info-*         feedback.warning → warning
//   feedback.success → success        feedback.danger  → danger
//   inverse.*        → term-*
//
// Таблица полная, и это утверждение, а не обещание: `tokens.test.ts` проверяет её в обе
// стороны — ни одной объявленной роли без класса, ни одного класса мимо роли.

import { palette, wash } from './primitives';

export const background = {
  // Земля, на которой стоит страница; заливка всего, что заполняется НА карточке
  // (дорожка прогресса, чип, счётчик); и разделитель строк таблицы. Три работы, одна
  // краска: заливка чипа и разделитель стояли в 1–3 единицах от неё, а это ниже порога,
  // на котором плоская область читается другим цветом.
  canvas: palette.warmGrey100,
  // Один шаг от белого: строка под курсором и коробка, которая приглашает в себя.
  surface: palette.warmGrey050,
  // Белая поверхность: карточка, диалог, панель, поле. Под альфой — она же над фотографией
  // и под размытием шапки (`bg-surface-card/85`).
  card: palette.white,
  // Завеса над ОДНОЙ фотографией: белый контроль поверх должен брать 4.5:1.
  scrim: wash.scrim,
  // Завеса над всей страницей: на ней ничего не пишут, она приглушает, а не контрастит.
  veil: wash.veil,
} as const;

export const content = {
  primary: palette.warmGrey900,
  secondary: palette.warmGrey800,
  // `muted` и `subtle` — два серых, которыми написан мелкий текст, и оба стоят на полу
  // AA, а не там, где смотрелись лучше: рампа сжата нарочно, потому что альтернатива —
  // ступень, про которую дизайн-система знает, что её нельзя прочесть.
  muted: palette.warmGrey700,
  subtle: palette.warmGrey600,
  // Чернила на ЛЮБОЙ заливке: действие, тон, нейтральная (`content-primary`,
  // `content-muted`), тёмная поверхность тоста и подсказки. Пол у каждой пары свой и
  // держится измерением в `contrast.test.ts`, а не отдельным именем: белый на `warning`
  // мерит 4.01:1, поэтому янтарь под надписью — всегда `warning-deep`.
  onFill: palette.white,
} as const;

export const border = {
  default: palette.warmGrey300,
  strong: palette.warmGrey400,
} as const;

export const action = {
  // Заливка действия, ссылка и индикатор фокуса (`outline-action-primary`).
  primary: palette.blue600,
  // Нажатие залитой кнопки: шаг ВНИЗ от заливки.
  primaryPressed: palette.blue700,
  // Приглушённые чернила НА залитом действии: дорожка кольца ожидания внутри кнопки.
  // Это композит белого 40% на `blue600` (#66a3ff), и `blue400` — ближайший рунг палитры
  // к нему: разница в 11 единиц красного не различима.
  onPrimaryTrack: palette.blue400,
} as const;

// Смысл, который сообщает интерфейс: `base` — текст и иконка, `strong` — самая тёмная
// ступень для надписи НА тонированной подложке, `pressed` — нажатие залитой кнопки этого
// тона, `tint` — сама подложка, `line` — её рамка. Чернила НА залитой ступени — общие,
// `content.onFill`.
//
// Набор у тона такой, какие работы этот тон действительно делает, а не симметричный.
//
// `strong` существует не для красоты: `success.base` на `success.tint` даёт 2.97:1,
// `warning.base` — 4.0:1 на белом, `danger.base` — 4.34:1 на своём тоне, а каждая плашка
// «удалён» в приложении набрана мелким. Пол AA — 4.5:1, и `strong` его берёт.
export const feedback = {
  // У «в работе» нет ни `base`, ни `pressed`: это тот же синий, что у действия, и носят
  // его `action-primary` и `action-pressed`. Зато есть `hairline`, которого нет ни у
  // одного другого тона.
  info: {
    strong: palette.blue800,
    // Подложка тона, она же наведение на незалитый контрол и выбранная плитка.
    tint: palette.blue050,
    // Рамка подложки; она же начало градиента заглушки медиа (`from-info-line to-line`).
    line: palette.blue200,
    // Рамка настолько бледная, что годится и как ЗАЛИВКА разделителя.
    hairline: palette.blue100,
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
// `black` остаётся техническим: его носят только под альфой над фотографией (`bg-black/55`
// на кадре истории, `border-black/5` на снимке), и своего значения у него нет ни у кого.
// Голый `bg-black` банит линтер. Белого технического нет: под альфой это та же белая
// поверхность, `surface-card`.
export const flatColors = {
  transparent: palette.transparent,
  current: palette.currentColor,
  black: palette.black,

  canvas: background.canvas,
  surface: {
    // Шаг от белого: строка под курсором, шапка таблицы, вложенный блок.
    DEFAULT: background.surface,
    // Белая поверхность: карточка, диалог, панель, поле ввода.
    card: background.card,
  },
  scrim: background.scrim,
  veil: background.veil,

  content: {
    primary: content.primary,
    secondary: content.secondary,
    muted: content.muted,
    subtle: content.subtle,
  },
  // Чернила на любой заливке. Отдельно от `surface-card` при том же #ffffff — см. шапку.
  'on-fill': content.onFill,
  // Дорожка кольца ожидания на залитом действии: `border-on-action-track`.
  'on-action-track': action.onPrimaryTrack,

  line: {
    DEFAULT: border.default,
    strong: border.strong,
  },
  action: {
    primary: action.primary,
    pressed: action.primaryPressed,
  },
  info: {
    // Единственный синий, который читается на `tint`.
    strong: feedback.info.strong,
    tint: feedback.info.tint,
    line: feedback.info.line,
    // PipelineCard берёт из одной краски обе работы — рамку карточки и фон сетки, чьи
    // 1px-щели И ЕСТЬ разделители плиток.
    hairline: feedback.info.hairline,
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
    // `press`, а не `strong`: у «в работе» `-strong` — это тёмная краска на тоне, и один
    // суффикс не должен значить два разных смысла в соседних тонах.
    press: feedback.warning.pressed,
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
