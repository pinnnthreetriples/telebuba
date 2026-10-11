// Building blocks every settings section uses: a numbered section header (title and a
// "?" — explanations live only in the hint), a setting row, and number fields that clamp
// to what the server accepts. The chat broadcast has the same row and fields; a page
// cannot import another page's UI, so this is the page's own copy.
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { useNumberField } from '@/shared/lib';
import { HelpHint, Input } from '@/shared/ui';

export function Section({
  n,
  title,
  hint,
  example,
  aside,
  children,
}: {
  n?: number;
  title: string;
  hint: string;
  example?: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        {n === undefined ? null : (
          <span className="flex size-chip items-center justify-center rounded-full bg-info-tint text-small font-medium text-info-strong">
            {n}
          </span>
        )}
        <span className="type-h3">{title}</span>
        <HelpHint text={hint} example={example} />
        {aside}
      </div>
      {children}
    </section>
  );
}

export function Row({
  label,
  hint,
  example,
  children,
  first = false,
}: {
  label: string;
  hint?: string;
  example?: string;
  children: ReactNode;
  first?: boolean;
}) {
  return (
    <div
      className={`flex min-h-touch flex-wrap items-center gap-3 py-2 ${first ? '' : 'border-t border-canvas'}`}
    >
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span className="text-body">{label}</span>
        {hint === undefined ? null : <HelpHint text={hint} example={example} />}
      </div>
      {children}
    </div>
  );
}

export function NumberField({
  value,
  label,
  min,
  max,
  unit,
  onChange,
}: {
  value: number;
  label: string;
  min: number;
  max: number;
  unit?: string;
  onChange: (value: number) => void;
}) {
  // An erased field stays empty until a number is typed; the draft keeps the old one.
  const field = useNumberField(
    value,
    (raw) => Math.min(max, Math.max(min, Math.round(raw))),
    onChange,
  );
  return (
    <span className="inline-flex items-center gap-2">
      <Input
        size="sm"
        className="w-number tabular-nums"
        type="number"
        min={min}
        max={max}
        value={field.value}
        aria-label={label}
        onChange={(event) => {
          field.onChange(event.target.value);
        }}
        onBlur={field.onBlur}
      />
      {unit === undefined ? null : <span className="type-small">{unit}</span>}
    </span>
  );
}

export function RangeField({
  value,
  unit,
  label,
  max,
  onChange,
}: {
  value: { min: number; max: number };
  unit: string;
  label: string;
  max: number;
  onChange: (value: { min: number; max: number }) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-2">
      <NumberField
        value={value.min}
        min={0}
        max={max}
        label={t('masstagging.settings.from', { label })}
        onChange={(next) => {
          onChange({ min: next, max: Math.max(next, value.max) });
        }}
      />
      <span className="type-small">—</span>
      <NumberField
        value={value.max}
        min={0}
        max={max}
        unit={unit}
        label={t('masstagging.settings.to', { label })}
        onChange={(next) => {
          onChange({ min: Math.min(value.min, next), max: next });
        }}
      />
    </div>
  );
}
