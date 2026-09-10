import { useTranslation } from 'react-i18next';

import { Icon, SegmentedControl } from '@/shared/ui';

import type { PrivacyKey, PrivacyLevel } from './_profileShared';
import { PRIVACY_KEYS, PRIVACY_LEVELS } from './_profileShared';

// Приватность: the three Telegram keys the single-account tab writes, applied to
// the batch — each behind its own checkbox, so an untouched key is left alone.
//
// No "current value" is shown, unlike the single-account tab. There isn't one:
// seventeen accounts hold seventeen answers, and rendering a summary would mean
// reading privacy from every selected account before the operator has decided to
// change anything. The caption says so rather than the UI implying a fleet has
// one setting.
export function BulkPrivacyTab({
  levels,
  onPick,
}: {
  levels: Partial<Record<PrivacyKey, PrivacyLevel>>;
  onPick: (key: PrivacyKey, level: PrivacyLevel | null) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-lg">
      <div className="rounded-lg bg-info-tint px-md py-md type-prose">
        {t('accounts.bulk.privacyHint')}
      </div>
      {PRIVACY_KEYS.map((key) => {
        const level = levels[key];
        const label = t(`accounts.profile.privacy.row.${key}`);
        return (
          <div key={key} className="flex flex-col gap-sm rounded-lg border border-line px-lg py-md">
            <button
              type="button"
              role="checkbox"
              aria-checked={level !== undefined}
              onClick={() => {
                onPick(key, level === undefined ? 'everybody' : null);
              }}
              className="flex items-center gap-md text-left"
            >
              <span
                className={`flex size-glyph shrink-0 items-center justify-center rounded-sm border ${level !== undefined ? 'border-action-primary bg-action-primary' : 'border-line bg-surface-card'}`}
              >
                {level !== undefined && (
                  <Icon name="check" size={14} className="stroke-on-action" />
                )}
              </span>
              <span className="type-card-title">{label}</span>
            </button>
            <SegmentedControl
              variant="outline"
              value={level ?? 'everybody'}
              disabled={level === undefined}
              ariaLabel={label}
              options={PRIVACY_LEVELS.map((option) => ({
                value: option,
                label: t(`accounts.profile.privacy.level.${option}`),
                ariaLabel: `${label}: ${t(`accounts.profile.privacy.level.${option}`)}`,
              }))}
              onChange={(next) => {
                onPick(key, next as PrivacyLevel);
              }}
            />
          </div>
        );
      })}
      <div className="type-caption">{t('accounts.bulk.privacyNote')}</div>
    </div>
  );
}
