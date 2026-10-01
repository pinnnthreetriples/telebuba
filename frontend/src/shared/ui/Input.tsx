import { useCallback, useLayoutEffect, useRef } from 'react';
import type { InputHTMLAttributes, Ref, TextareaHTMLAttributes } from 'react';

import {
  areaBase,
  type ControlSize,
  fieldBase,
  fieldInset,
  type FieldInset,
  type FieldVariant,
  type AreaVariant,
  fieldPresentation,
  areaPresentation,
  fieldWidth,
} from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

// The app's text fields. The `md` look below was copy-pasted verbatim as a local
// `FIELD`/`INPUT` const in four files and with one word changed in four more, so
// the look drifted where nobody meant it to: `box-border` in one, no focus
// transition in another.
//
// Высота, поля, рунг размера, форма, фокус, `invalid` и переход приходят из
// `recipes/controls.ts` — того же рецепта, что у Button и Select. `md` теперь ровно
// столько же, сколько `Button size="md"`; раньше было 41px против 40px, и оба числа были
// СУММОЙ padding и интерлиньяжа, то есть менялись от смены рунга.
//
// `md` — собственное поле формы; `sm` — поле внутри строки карточки, где `md` задал бы
// высоту строки; `xs` — числовой степпер, в который значение вписывают рядом с единицей.
//
// Textarea берёт `areaBase`: её высота следует за содержимым, а не за числом строк или
// ручным перетаскиванием. Всё остальное — то же самое.
//
// `lg` (цель касания) полю не предлагается: 44px — высота, которую носит мобильная
// навигация, а не поле в форме, и ступень без носителя открыла бы шкалу обратно.

// `flat` is the field that is not for typing into — a fact being displayed, or a
// secret shown once to be read off the screen. It keeps the canvas fill so it
// reads as inert, and `invalid` overrides either.
const TONE = {
  default: 'border-line',
  flat: 'border-line bg-canvas',
  selected: 'border-action-primary',
  inert: 'border-line bg-canvas text-content-subtle',
} as const;

type FieldSize = Exclude<ControlSize, 'lg'>;

type Shared<Variant extends FieldVariant | AreaVariant = FieldVariant | AreaVariant> = {
  variant?: Variant;
  widthPreset?: Parameters<typeof fieldWidth>[0];
  textStyle?: 'default' | 'mono' | 'code' | 'tabular' | 'monoCode' | 'monoPrimary';
  size?: FieldSize;
  tone?: keyof typeof TONE;
  // Drives the border only. The message itself belongs beside the field (see
  // `FieldError`), because a red border alone is a colour carrying meaning.
  invalid?: boolean;
  className?: string;
  inset?: FieldInset;
};

function shell(
  {
    size = 'md',
    tone = 'default',
    invalid,
    className,
    inset = 'none',
    variant = 'standard',
    textStyle = 'default',
    widthPreset,
  }: Shared,
  multiline = false,
): string {
  const base =
    variant === 'standard'
      ? multiline
        ? areaBase({ size, invalid })
        : fieldBase({ size, invalid })
      : variant === 'prompt' || variant === 'composer'
        ? areaPresentation(variant)
        : fieldPresentation(variant);
  const TEXT_STYLE = {
    default: '',
    mono: 'font-mono',
    code: 'tracking-code',
    tabular: 'tabular-nums',
    monoCode: 'font-mono tracking-code',
    monoPrimary: 'font-mono text-content-primary',
  };
  return cn(
    base,
    TONE[tone],
    TEXT_STYLE[textStyle],
    widthPreset && fieldWidth(widthPreset),
    invalid === true && 'border-danger',
    fieldInset(inset),
    className,
  );
}

export function Input({
  size,
  tone,
  invalid,
  className,
  inset,
  variant,
  textStyle,
  widthPreset,
  ...rest
}: Shared<FieldVariant> & InputProps) {
  return (
    <input
      aria-invalid={invalid || undefined}
      className={shell({ size, tone, invalid, className, inset, variant, textStyle, widthPreset })}
      {...rest}
    />
  );
}

function fitTextarea(area: HTMLTextAreaElement, maxRows?: number) {
  area.style.height = 'auto';
  if (area.scrollHeight <= 0) return;
  if (maxRows === undefined) {
    area.style.height = `${String(area.scrollHeight)}px`;
    return;
  }
  const style = getComputedStyle(area);
  const line = Number.parseFloat(style.lineHeight);
  const padding = Number.parseFloat(style.paddingTop) + Number.parseFloat(style.paddingBottom);
  const cap =
    Math.max(1, maxRows) * (Number.isFinite(line) && line > 0 ? line : 20) +
    (Number.isFinite(padding) ? padding : 0);
  area.style.height = `${String(Math.min(area.scrollHeight, cap))}px`;
  area.style.overflowY = area.scrollHeight > cap ? 'auto' : 'hidden';
}

export function Textarea({
  size,
  tone,
  invalid,
  className,
  inset,
  variant,
  maxRows,
  textStyle,
  widthPreset,
  onInput,
  ref: forwardedRef,
  ...rest
}: Shared<AreaVariant> & TextareaProps) {
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const attachRef = useCallback(
    (node: HTMLTextAreaElement | null) => {
      areaRef.current = node;
      if (typeof forwardedRef === 'function') forwardedRef(node);
      else if (forwardedRef) forwardedRef.current = node;
    },
    [forwardedRef],
  );

  useLayoutEffect(() => {
    if (areaRef.current) fitTextarea(areaRef.current, maxRows);
  });

  useLayoutEffect(() => {
    const area = areaRef.current;
    if (!area || typeof ResizeObserver === 'undefined') return;
    let width = area.clientWidth;
    const observer = new ResizeObserver(() => {
      if (area.clientWidth === width) return;
      width = area.clientWidth;
      fitTextarea(area, maxRows);
    });
    observer.observe(area);
    return () => observer.disconnect();
  }, [maxRows]);

  return (
    <textarea
      aria-invalid={invalid || undefined}
      {...rest}
      rows={1}
      ref={attachRef}
      className={cn(
        shell({ size, tone, invalid, className, inset, variant, textStyle, widthPreset }, true),
        'resize-none overflow-hidden',
      )}
      onInput={(event) => {
        fitTextarea(event.currentTarget, maxRows);
        onInput?.(event);
      }}
    />
  );
}

// `ref` rides along as an ordinary prop (React 19); the OTP field focuses itself.
type InputProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'size' | 'className'> & {
  ref?: Ref<HTMLInputElement>;
};
type TextareaProps = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'className' | 'rows'> & {
  maxRows?: number;
  ref?: Ref<HTMLTextAreaElement>;
};
