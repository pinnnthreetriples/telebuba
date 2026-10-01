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
import { componentSettings as settings } from '../tokens/components';
import type { ControlSize, FieldVariant, AreaVariant } from '../tokens/components';
import {
  GAP,
  HEIGHT,
  PL,
  PX,
  PY,
  RADIUS,
  ROLE,
  TEXT,
  WIDE_PX,
  WIDTH,
  paddingClasses,
} from './settings';
export type { ControlSize } from '../tokens/components';

// Высота — ФИКСИРОВАННАЯ, и это главное, что рецепт приносит. Четыре ступени, каждая
// называет, где контрол стоит: `xs` — короче поля, `sm` — внутри строки, `md` —
// самостоятельный контрол формы, `lg` — цель касания.

// Рунг размера по ступени — тоже общий: это то, что делает имя ступени одним и тем же у
// кнопки и у поля.
//
// Все четыре ступени набраны `body`, и это не заготовка под различие, а следствие
// слияния: `xs`/`sm` были `body` (12.5px), `md`/`lg` — `lead` (13px), то есть полшага
// разницы, которой на контроле не видно. Ступени различает высота, а не кегль — она
// теперь фиксированная, и её видно.

// Форма — по РОДУ контрола, а не по его ступени размера, и это правка, а не описание.
//
// Раньше `Button` выбирал форму по ступени: `md`/`sm` — пилюля, `xs` — `inset`, `block` —
// `field`. То есть размер решал форму, и «сделать кнопку меньше» означало «сделать кнопку
// другой формы». Объяснения у обоих исключений были, и оба не выдержали проверки счётом:
// «на 28px полный радиус и прямоугольник — одна форма» неверно (14px против 8px радиуса
// видно рядом), а «пилюля на 200px была бы стадионом» — это про вкус, и 83 пилюли из 104
// кнопок приложения с ним не согласны.
//
// Теперь у кнопки одна форма на все ступени. Ступень отвечает за высоту и поля — и только.

// Фокус — ОБВОДКА, а не тень. Тенью он и был, и `shadow-focus` на белом мерит **1.18:1**
// против 3:1, которых WCAG 2.2 требует от индикатора фокуса; вдобавок он приходил от
// самой кнопки, а не от `:focus-visible`, поэтому «фокуса нет» и «фокус есть, но не
// виден» выглядели одинаково.
//
// `shadow-focus` сохраняет работу на ПОЛЯХ, где это свечение рядом с меняющей цвет
// рамкой, а не единственный признак.
// Экспортируется — единственная из внутренних строк этого файла. Рецепты `buttonBase`
// и `fieldBase` собирают контрол целиком, а `IconButton` и `SegmentedControl` собой не
// являются контролами этой формы (у них своя высота и свой контракт доступности) и брали
// те же четыре класса СПИСКОМ. Решение о фокусе одно, и место у него теперь тоже одно.
export const FOCUS_RING =
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus';

// Поле анимирует рамку и свечение через `.tb-time` (index.css) — общий рецепт, а не класс
// на каждом поле, и `:focus-within`, а не `:focus`, потому что поле бывает обёрткой
// вокруг настоящего `<input>`.
const FIELD_FOCUS = 'tb-time outline-none';

const DISABLED = 'disabled:pointer-events-none disabled:opacity-50';

export const PRESS_FEEDBACK =
  'active:scale-press disabled:active:scale-rest aria-busy:active:scale-rest motion-reduce:active:scale-rest';

// Невалидность рисуется рамкой, и только ею: сообщение стоит рядом с полем
// (`FieldError`), потому что красная рамка сама по себе — цвет, несущий смысл.
const INVALID = 'border-danger';

const CONTROL_TRANSITION = 'transition duration-state';

// `ControlShape` тут был и ушёл вместе с параметром `shape` у `buttonBase`: тип, который
// называет выбор, которого больше нет, — это приглашение вернуть выбор.

/**
 * Кнопка: фиксированная высота, поля, рунг, ПИЛЮЛЯ, обводка фокуса и переход.
 *
 * Форма не параметр. Она им была — `shape: ControlShape`, — и единственный вызывающий
 * передавал её из таблицы размеров, то есть параметр существовал ровно для того, чтобы
 * размер мог менять форму. Убрать его — и подменить форму кнопки становится нельзя ни из
 * компонента, ни из места вызова.
 */
export function buttonBase({ size, className }: { size: ControlSize; className?: string }): string {
  return cn(
    'inline-flex shrink-0 items-center justify-center whitespace-nowrap',
    GAP[settings.controls.gap],
    HEIGHT[settings.controls.height[size]],
    PX[settings.controls.buttonPadding[size]],
    TEXT[settings.controls.text[size]],
    RADIUS[settings.controls.buttonRadius],
    FOCUS_RING,
    DISABLED,
    PRESS_FEEDBACK,
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
    HEIGHT[settings.controls.height[size]],
    PX[settings.controls.fieldPadding[size]],
    TEXT[settings.controls.text[size]],
    RADIUS[settings.controls.fieldRadius[size]],
    FIELD_FOCUS,
    CONTROL_TRANSITION,
    invalid === true && INVALID,
    className,
  );
}

/** Многострочное поле: всё то же, КРОМЕ высоты — она следует за содержимым. */
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
    // Вертикальные поля вместо высоты: область растёт вслед за текстом, и фиксировать
    // её значило бы обрезать написанное. Значения подобраны так, чтобы
    // однострочная область совпала по высоте с полем той же ступени.
    PY[settings.controls.areaPadding[size]],
    PX[settings.controls.fieldPadding[size]],
    TEXT[settings.controls.text[size]],
    RADIUS[settings.controls.fieldRadius[size]],
    FIELD_FOCUS,
    CONTROL_TRANSITION,
    invalid === true && INVALID,
    className,
  );
}

const WEIGHT = { medium: 'font-medium', semibold: 'font-semibold' } as const;
export function buttonWeight(size: ControlSize): string {
  return WEIGHT[settings.controls.buttonWeight[size]];
}
export function buttonLoadingGap(): string {
  return GAP[settings.controls.loadingGap];
}

export function buttonContentGap(gap: 'default' | 'roomy'): string {
  return GAP[gap === 'default' ? settings.controls.gap : settings.controls.roomyGap];
}
export type FieldInset = 'none' | 'leading' | 'trailing' | 'both';
export function fieldInset(inset: FieldInset): string {
  return cn(
    (inset === 'leading' || inset === 'both') && PL[settings.controls.iconInset],
    (inset === 'trailing' || inset === 'both') && 'field-end-inset',
  );
}

const WIDE_HEIGHT = {
  field: 'sm:h-field',
  control: 'sm:h-control',
  touch: 'sm:h-touch',
  compact: 'sm:h-compact',
};
const MIN_HEIGHT = { touch: 'min-h-touch', control: 'min-h-control' };
export type ButtonPresentation =
  'standard' | 'multiline' | 'touch' | 'compactFooter' | 'status' | 'tab';
export function buttonPresentation(
  presentation: ButtonPresentation,
  shape: 'pill' | 'square',
  size: ControlSize,
): string {
  const config = settings.controls;
  return cn(
    shape === 'square' && RADIUS[config.squareRadius],
    (presentation === 'multiline' || presentation === 'touch') &&
      cn(
        'h-auto sm:min-h-0',
        MIN_HEIGHT[config.multiline.minHeight as keyof typeof MIN_HEIGHT],
        WIDE_HEIGHT[config.multiline.wideHeight as keyof typeof WIDE_HEIGHT],
      ),
    presentation === 'multiline' &&
      cn(
        'whitespace-normal sm:py-0',
        PX[config.multiline.x],
        PY[config.multiline.y],
        WIDE_PX[config.multiline.wideX],
      ),
    presentation === 'status' &&
      cn('h-auto font-medium', PX[config.buttonPadding.xs], PY[config.areaPadding.xs]),
    presentation === 'tab' && cn('h-auto min-h-touch font-semibold', PX[config.fieldPadding.lg]),
    presentation === 'compactFooter' &&
      cn(PX[config.compactFooterX], WIDE_PX[config.buttonPadding[size]]),
  );
}
export function fieldPresentation(variant: Exclude<FieldVariant, 'standard'>): string {
  return cn(
    'w-full outline-none',
    RADIUS[settings.controls.variantRadii[variant]],
    paddingClasses(settings.controls.fieldVariants[variant]),
    variant === 'inlineCaption'
      ? 'border-none bg-transparent type-caption'
      : variant === 'inline'
        ? 'border-none bg-transparent text-body'
        : cn('border bg-surface-card text-body', FIELD_FOCUS),
    variant === 'readout' && 'font-mono font-semibold',
    variant === 'compactNumber' && 'font-medium',
    variant === 'auth' && 'font-normal text-content-primary',
  );
}
export function areaPresentation(variant: Exclude<AreaVariant, 'standard'>): string {
  return cn(
    'w-full border bg-surface-card outline-none',
    RADIUS[settings.controls.areaRadii[variant]],
    FIELD_FOCUS,
    paddingClasses(settings.controls.areaVariants[variant]),
    variant === 'composer'
      ? cn(
          MIN_HEIGHT[settings.controls.composerMinHeight],
          ROLE[settings.controls.areaText.composer],
          'text-content-primary',
        )
      : TEXT[settings.controls.areaText.prompt],
  );
}
export function fieldWidth(preset: keyof typeof settings.controls.fieldWidths): string {
  return WIDTH[settings.controls.fieldWidths[preset]];
}
