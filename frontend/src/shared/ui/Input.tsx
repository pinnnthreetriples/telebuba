import { useCallback, useLayoutEffect, useRef } from 'react';
import type { InputHTMLAttributes, Ref, TextareaHTMLAttributes } from 'react';

import { areaBase, type ControlSize, fieldBase } from '@/shared/design-system';
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
// высоту строки, и числовой степпер, в который значение вписывают рядом с единицей (он
// носил `xs` в 28px, пока ступень не ушла из шкалы).
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
} as const;

type FieldSize = Exclude<ControlSize, 'lg'>;

type Shared = {
  size?: FieldSize;
  tone?: keyof typeof TONE;
  // Drives the border only. The message itself belongs beside the field (see
  // `FieldError`), because a red border alone is a colour carrying meaning.
  invalid?: boolean;
  className?: string;
};

function shell(
  { size = 'md', tone = 'default', invalid, className }: Shared,
  multiline = false,
): string {
  const base = multiline ? areaBase({ size, invalid }) : fieldBase({ size, invalid });
  return cn(base, TONE[tone], invalid === true && 'border-danger', className);
}

export function Input({ size, tone, invalid, className, ...rest }: Shared & InputProps) {
  return (
    <input
      aria-invalid={invalid || undefined}
      className={shell({ size, tone, invalid, className })}
      {...rest}
    />
  );
}

function fitTextarea(area: HTMLTextAreaElement) {
  area.style.height = 'auto';
  if (area.scrollHeight > 0) area.style.height = `${String(area.scrollHeight)}px`;
}

export function Textarea({
  size,
  tone,
  invalid,
  className,
  onInput,
  ref: forwardedRef,
  ...rest
}: Shared & TextareaProps) {
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
    if (areaRef.current) fitTextarea(areaRef.current);
  });

  useLayoutEffect(() => {
    const area = areaRef.current;
    if (!area || typeof ResizeObserver === 'undefined') return;
    let width = area.clientWidth;
    const observer = new ResizeObserver(() => {
      if (area.clientWidth === width) return;
      width = area.clientWidth;
      fitTextarea(area);
    });
    observer.observe(area);
    return () => observer.disconnect();
  }, []);

  return (
    <textarea
      aria-invalid={invalid || undefined}
      {...rest}
      rows={1}
      ref={attachRef}
      className={cn(shell({ size, tone, invalid, className }, true), 'resize-none overflow-hidden')}
      onInput={(event) => {
        fitTextarea(event.currentTarget);
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
  ref?: Ref<HTMLTextAreaElement>;
};
