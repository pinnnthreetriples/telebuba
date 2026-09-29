import { useId } from 'react';
import { useTranslation } from 'react-i18next';

import { Input } from '@/shared/ui';

import type { NeuroLimitsField, NeuroLimitsValue } from './neuroLimitsForm';

const FIELD_LABEL = 'mb-tight block type-label';
const ERROR = 'mt-tight block text-tiny font-medium text-danger-deep';

// The fleet-wide neurocomment limits — moved here from the Settings page, next to the
// comment mode they pace. Controlled and write-free like `CommentModeFields`: the modal
// owns the draft and the single PUT, so "Отмена" can throw the edit away.
export function NeuroLimitsFields({
  value,
  errors,
  disabled,
  onChange,
}: {
  value: NeuroLimitsValue;
  errors: Partial<Record<NeuroLimitsField, string>>;
  disabled: boolean;
  onChange: (field: NeuroLimitsField, raw: string) => void;
}) {
  const { t } = useTranslation();
  const baseId = useId();
  const errorId = (field: NeuroLimitsField) => `${baseId}-${field}-error`;

  const field = (name: NeuroLimitsField, label: string, inputMode: 'numeric' | 'decimal') => (
    <Input
      inputMode={inputMode}
      value={value[name]}
      disabled={disabled}
      invalid={errors[name] !== undefined}
      aria-label={label}
      aria-describedby={errors[name] === undefined ? undefined : errorId(name)}
      onChange={(event) => {
        onChange(name, event.target.value);
      }}
    />
  );
  const error = (name: NeuroLimitsField) =>
    errors[name] === undefined ? null : (
      <span id={errorId(name)} className={ERROR}>
        {t(errors[name])}
      </span>
    );
  const labelled = (name: NeuroLimitsField, labelKey: string) => (
    <label className="block min-w-0">
      <span className={FIELD_LABEL}>{t(labelKey)}</span>
      {field(name, t(labelKey), 'numeric')}
      {error(name)}
    </label>
  );

  return (
    <div className="grid grid-cols-1 gap-md md:grid-cols-2">
      {labelled('cpd', 'neurocomment.limits.cpd')}
      <div className="min-w-0">
        <span className={FIELD_LABEL}>{t('neurocomment.limits.delay')}</span>
        <div className="flex items-center gap-sm">
          {field('delayFrom', t('neurocomment.limits.delayFrom'), 'decimal')}
          <span className="shrink-0 type-caption">—</span>
          {field('delayTo', t('neurocomment.limits.delayTo'), 'decimal')}
        </div>
        {error('delayFrom')}
        {error('delayTo')}
      </div>
      {labelled('parallel', 'neurocomment.limits.parallel')}
      {labelled('trust', 'neurocomment.limits.trust')}
    </div>
  );
}
