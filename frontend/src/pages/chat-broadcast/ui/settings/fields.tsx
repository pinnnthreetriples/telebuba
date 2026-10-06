// Building blocks every settings section uses: the eyebrow over a section, a setting row
// (label and "?" on the left, control on the right — explanations live only in the hint),
// and controlled number fields that clamp to what the server accepts.
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { HelpHint, Input } from '@/shared/ui';

export function Eyebrow({
  title,
  caption,
  hint,
}: {
  title: string;
  caption?: string;
  hint?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-sm pb-sm">
      <span className="type-eyebrow">{title}</span>
      {caption === undefined ? null : <span className="type-caption">{caption}</span>}
      {hint}
    </div>
  );
}

export function Row({
  label,
  hint,
  example,
  children,
  first = false,
  stack = false,
}: {
  label: string;
  hint?: string;
  example?: string;
  children: ReactNode;
  first?: boolean;
  // A wide control (many options) goes under the label instead of squeezing it.
  stack?: boolean;
}) {
  return (
    <div
      className={`flex min-h-touch flex-wrap items-center gap-md py-sm ${first ? '' : 'border-t border-line-row'}`}
    >
      <div className={`flex min-w-0 flex-1 items-center gap-sm ${stack ? 'basis-full' : ''}`}>
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
  onChange,
}: {
  value: number;
  label: string;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  return (
    <Input
      size="xs"
      className="w-number tabular-nums"
      type="number"
      min={min}
      max={max}
      value={value}
      aria-label={label}
      onChange={(event) => {
        const parsed = Math.round(Number(event.target.value));
        onChange(Number.isFinite(parsed) ? Math.min(max, Math.max(min, parsed)) : min);
      }}
    />
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
    <div className="flex items-center gap-sm">
      <NumberField
        value={value.min}
        min={0}
        max={max}
        label={t('chatBroadcast.settings.pace.from', { label })}
        onChange={(next) => {
          onChange({ min: next, max: Math.max(next, value.max) });
        }}
      />
      <span className="type-caption">—</span>
      <NumberField
        value={value.max}
        min={0}
        max={max}
        label={t('chatBroadcast.settings.pace.to', { label })}
        onChange={(next) => {
          onChange({ min: Math.min(value.min, next), max: next });
        }}
      />
      <span className="type-caption">{unit}</span>
    </div>
  );
}
