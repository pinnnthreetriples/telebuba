import type { InputHTMLAttributes, Ref, TextareaHTMLAttributes } from 'react';

import { cn } from '@/shared/lib/cn';

// The app's text fields. The `md` look below was copy-pasted verbatim as a local
// `FIELD`/`INPUT` const in four files and with one word changed in four more, so
// the look drifted where nobody meant it to: `box-border` in one, no focus
// transition in another.
//
// `tb-time` is the shared focus treatment (index.css): it animates the border and
// paints `shadow-focus` on `:focus-within`, which is why the fields set
// `outline-none` — the ring IS the outline, and it follows a field wrapped in a
// row of its own (a password field with a reveal button) rather than only the
// input.
const BASE = 'tb-time w-full rounded-lg border bg-white outline-none';

// `md` is a form's own field; `sm` a field inside a card's row, where `md` would
// set the row height; `xs` the numeric stepper a value is typed into beside its
// unit.
const SIZE = {
  md: 'px-md py-md text-lead',
  sm: 'px-md py-sm text-body',
  xs: 'rounded-md px-md py-tight text-body',
} as const;

// `flat` is the field that is not for typing into — a fact being displayed, or a
// secret shown once to be read off the screen. It keeps the canvas fill so it
// reads as inert, and `invalid` overrides either.
const TONE = {
  default: 'border-line',
  flat: 'border-line bg-canvas',
} as const;

type Shared = {
  size?: keyof typeof SIZE;
  tone?: keyof typeof TONE;
  // Drives the border only. The message itself belongs beside the field (see
  // `FieldError`), because a red border alone is a colour carrying meaning.
  invalid?: boolean;
  className?: string;
};

function shell({ size = 'md', tone = 'default', invalid, className }: Shared): string {
  return cn(BASE, SIZE[size], TONE[tone], invalid && 'border-danger', className);
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
      className={shell({ size, tone, invalid, className })}
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
