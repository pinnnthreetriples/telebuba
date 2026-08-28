import type { InputHTMLAttributes, ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Input } from './Input';

// Minimal @tanstack/react-form field primitive: a label, an input (or arbitrary
// child), and the field's first validation error. Shared so every migrated form
// (proxy add/edit, profile text, add-account) displays errors the same way.
// `cn` is imported from the specific module (not the shared/lib barrel) to avoid
// the shared/ui ↔ shared/lib import cycle.
const LABEL = 'mb-tight block text-body font-medium text-content-secondary';

// Structural slice of a react-form string field — just what this primitive reads
// and calls. Avoids depending on the library's exact FieldApi generics/export.
export interface FormFieldApi {
  name: string;
  state: { value: string; meta: { isTouched: boolean; errors: unknown[] } };
  handleChange: (value: string) => void;
  handleBlur: () => void;
}

// The first standard-schema error message for a field, or null when valid.
function fieldError(field: FormFieldApi): string | null {
  if (!field.state.meta.isTouched) return null;
  const [first] = field.state.meta.errors;
  if (first == null) return null;
  if (typeof first === 'string') return first;
  const message = (first as { message?: unknown }).message;
  return typeof message === 'string' ? message : null;
}

// zod messages are stored as i18n keys, so the visible error is resolved via t().
export function FieldError({ field }: { field: FormFieldApi }) {
  const { t } = useTranslation();
  const error = fieldError(field);
  if (!error) return null;
  return <span className="mt-tight block text-tiny font-medium text-danger">{t(error)}</span>;
}

// A labelled text input bound to a react-form field. `label` may be omitted when
// the caller lays out its own label (then only the input + error render).
export function FormField({
  field,
  label,
  className,
  children,
  ...rest
}: {
  field: FormFieldApi;
  label?: string;
  children?: ReactNode;
  // `size` is dropped along with the bound three: the HTML attribute is a
  // character count no field in this app sets, and `Input` spends the name on its
  // own scale.
} & Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'onBlur' | 'size'>) {
  const invalid = fieldError(field) !== null;
  return (
    <label className="block">
      {label ? <span className={LABEL}>{label}</span> : null}
      {children ?? (
        <Input
          id={field.name}
          name={field.name}
          value={field.state.value}
          onChange={(event) => {
            field.handleChange(event.target.value);
          }}
          onBlur={field.handleBlur}
          invalid={invalid}
          className={className}
          {...rest}
        />
      )}
      <FieldError field={field} />
    </label>
  );
}
