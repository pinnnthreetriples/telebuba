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
// высоту строки; `xs` — числовой степпер, в который значение вписывают рядом с единицей.
//
// Textarea берёт `areaBase`: у неё высота приходит из `rows`, и фиксировать её значило бы
// обрезать написанный текст. Всё остальное — то же самое.
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

export function Textarea({ size, tone, invalid, className, ...rest }: Shared & TextareaProps) {
  return (
    <textarea
      aria-invalid={invalid || undefined}
      className={shell({ size, tone, invalid, className }, true)}
      {...rest}
    />
  );
}

// `ref` rides along as an ordinary prop (React 19); the OTP field focuses itself.
type InputProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'size' | 'className'> & {
  ref?: Ref<HTMLInputElement>;
};
type TextareaProps = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'className'> & {
  ref?: Ref<HTMLTextAreaElement>;
};
