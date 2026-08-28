// Общий фундамент контрола: то, в чём Button, Input, Textarea и Select ОБЯЗАНЫ совпадать.
//
// Совпадать они обязаны в высоте, рунге размера, фокусе, disabled, invalid и переходе.
// Каждый решал это сам, и расхождения были не решениями: `box-border` в одном файле и не
// в другом, фокус в трёх компонентах из пяти, и высоты, которые СКЛАДЫВАЛИСЬ из padding и
// интерлиньяжа — то есть менялись от смены рунга. `Button size="md"` был ~40px, а
// `Input size="md"` ~41px, при том что имя обещало одно.
//
// ── Что рецепт НЕ забирает, и почему ───────────────────────────────────────────────
//
// Форму. Кнопка — пилюля, поле — прямоугольник с `rounded-lg`. Это различие
// дизайн-источника, а не дрейф: у них разные аффордансы, и слить их значило бы либо
// сделать поля пилюлями, либо кнопки коробками.
//
// Горизонтальные поля. У пилюли они шире, чем у поля ввода, и это следствие формы: у
// круглого торца текст должен отступить от края дуги. `BUTTON_PAD` и `FIELD_PAD` стоят
// рядом здесь же — не общие, но и не спрятанные по компонентам.
//
// Заливку и краску. Это `VARIANT`/`TONE` компонента: чем контрол ЗАЛИТ — его собственное
// решение, а высота и фокус — нет.
//
// Button и IconButton не объединены и объединены не будут: у них разные контракты
// доступности. У обычной кнопки имя — её содержимое, у иконочной оно приходит из
// `aria-label`, и общий компонент сделал бы это имя необязательным.
import { cn } from '@/shared/lib/cn';

// Высота — ФИКСИРОВАННАЯ, и это главное, что рецепт приносит. Четыре ступени, каждая
// называет, где контрол стоит: `xs` — короче поля, `sm` — внутри строки, `md` —
// самостоятельный контрол формы, `lg` — цель касания.
const CONTROL_HEIGHT = {
  xs: 'h-compact',
  sm: 'h-field',
  md: 'h-control',
  lg: 'h-touch',
} as const;

// Рунг размера по ступени — тоже общий: это то, что делает имя ступени одним и тем же у
// кнопки и у поля.
//
// Все четыре ступени набраны `body`, и это не заготовка под различие, а следствие
// слияния: `xs`/`sm` были `body` (12.5px), `md`/`lg` — `lead` (13px), то есть полшага
// разницы, которой на контроле не видно. Ступени различает высота, а не кегль — она
// теперь фиксированная, и её видно.
const CONTROL_TEXT = {
  xs: 'text-body',
  sm: 'text-body',
  md: 'text-body',
  lg: 'text-body',
} as const;

const BUTTON_PAD = {
  xs: 'px-md',
  sm: 'px-xl',
  md: 'px-2xl',
  lg: 'px-2xl',
} as const;

const FIELD_PAD = {
  xs: 'px-md',
  sm: 'px-md',
  md: 'px-md',
  lg: 'px-lg',
} as const;

const SHAPE = {
  // Кнопка.
  pill: 'rounded-full',
  // Поле, триггер выпадающего списка, кнопка во всю ширину блока.
  field: 'rounded-lg',
  // Контрол внутри другой коробки.
  inset: 'rounded-md',
} as const;

// Фокус — ОБВОДКА, а не тень. Тенью он и был, и `shadow-focus` на белом мерит **1.18:1**
// против 3:1, которых WCAG 2.2 требует от индикатора фокуса; вдобавок он приходил от
// самой кнопки, а не от `:focus-visible`, поэтому «фокуса нет» и «фокус есть, но не
// виден» выглядели одинаково.
//
// `shadow-focus` сохраняет работу на ПОЛЯХ, где это свечение рядом с меняющей цвет
// рамкой, а не единственный признак.
const FOCUS_RING =
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action-primary';

// Поле анимирует рамку и свечение через `.tb-time` (index.css) — общий рецепт, а не класс
// на каждом поле, и `:focus-within`, а не `:focus`, потому что поле бывает обёрткой
// вокруг настоящего `<input>`.
const FIELD_FOCUS = 'tb-time outline-none';

const DISABLED = 'disabled:pointer-events-none disabled:opacity-50';

// Невалидность рисуется рамкой, и только ею: сообщение стоит рядом с полем
// (`FieldError`), потому что красная рамка сама по себе — цвет, несущий смысл.
const INVALID = 'border-danger';

const CONTROL_TRANSITION = 'transition-colors duration-state';

export type ControlSize = keyof typeof CONTROL_HEIGHT;
export type ControlShape = keyof typeof SHAPE;

/** Кнопка: фиксированная высота, рунг, форма, обводка фокуса и переход. */
export function buttonBase({
  size,
  shape,
  className,
}: {
  size: ControlSize;
  shape: ControlShape;
  className?: string;
}): string {
  return cn(
    'inline-flex shrink-0 items-center justify-center gap-tight whitespace-nowrap',
    CONTROL_HEIGHT[size],
    BUTTON_PAD[size],
    CONTROL_TEXT[size],
    SHAPE[shape],
    FOCUS_RING,
    DISABLED,
    CONTROL_TRANSITION,
    className,
  );
}

/** Поле ввода: та же высота и рунг, свечение фокуса вместо обводки. */
export function fieldBase({
  size,
  invalid,
  className,
}: {
  size: ControlSize;
  invalid?: boolean;
  className?: string;
}): string {
  return cn(
    'w-full border bg-surface-card',
    CONTROL_HEIGHT[size],
    FIELD_PAD[size],
    CONTROL_TEXT[size],
    SHAPE[size === 'xs' ? 'inset' : 'field'],
    FIELD_FOCUS,
    CONTROL_TRANSITION,
    invalid === true && INVALID,
    className,
  );
}

/** Многострочное поле: всё то же, КРОМЕ высоты — её задаёт `rows`. */
export function areaBase({
  size,
  invalid,
  className,
}: {
  size: ControlSize;
  invalid?: boolean;
  className?: string;
}): string {
  return cn(
    'w-full border bg-surface-card',
    // Вертикальные поля вместо высоты: у области высота приходит из `rows`, и фиксировать
    // её значило бы обрезать написанный в неё текст. Значения подобраны так, чтобы
    // однострочная область совпала по высоте с полем той же ступени.
    size === 'md' || size === 'lg' ? 'py-sm' : 'py-tight',
    FIELD_PAD[size],
    CONTROL_TEXT[size],
    SHAPE[size === 'xs' ? 'inset' : 'field'],
    FIELD_FOCUS,
    CONTROL_TRANSITION,
    invalid === true && INVALID,
    className,
  );
}
