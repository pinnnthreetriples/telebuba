import { useTranslation } from 'react-i18next';

import { Switch } from '@/shared/ui';

import type { FullProtection } from '../model/protection';
import { NumberField } from './ScheduleSection';

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex items-center justify-between gap-3 type-small">
      <span>{label}</span>
      <Switch checked={checked} onChange={onChange} label={label} />
    </label>
  );
}

// "Account protection" and "Like a human": the run's own copy of the saved switches.
export function ProtectionSection({
  protection,
  onChange,
}: {
  protection: FullProtection;
  onChange: (protection: FullProtection) => void;
}) {
  const { t } = useTranslation();
  const set = (patch: Partial<FullProtection>) => {
    onChange({ ...protection, ...patch });
  };

  return (
    <>
      <section className="space-y-3">
        <h4 className="type-body-medium text-content-secondary">
          {t('accounts.messages.protection.title')}
        </h4>
        <Toggle
          label={t('accounts.messages.protection.young', {
            days: protection.young_account_days,
          })}
          checked={protection.young_half_caps}
          onChange={(value) => {
            set({ young_half_caps: value });
          }}
        />
        <Toggle
          label={t('accounts.messages.protection.rest')}
          checked={protection.rest_after_limit}
          onChange={(value) => {
            set({ rest_after_limit: value });
          }}
        />
        {protection.rest_after_limit && (
          <div className="flex flex-wrap items-center gap-2 type-small">
            <span>{t('accounts.messages.protection.restHours')}</span>
            <NumberField
              label={t('accounts.messages.protection.restHours')}
              value={protection.rest_hours}
              min={1}
              max={168}
              onChange={(value) => {
                set({ rest_hours: value });
              }}
            />
            <span>{t('accounts.messages.protection.hours')}</span>
          </div>
        )}
        <Toggle
          label={t('accounts.messages.protection.stop')}
          checked={protection.stop_run_on_limits}
          onChange={(value) => {
            set({ stop_run_on_limits: value });
          }}
        />
        {protection.stop_run_on_limits && (
          <div className="flex flex-wrap items-center gap-2 type-small">
            <span>{t('accounts.messages.protection.stopAfter')}</span>
            <NumberField
              label={t('accounts.messages.protection.stopAfter')}
              value={protection.stop_run_after_accounts}
              min={1}
              max={50}
              onChange={(value) => {
                set({ stop_run_after_accounts: value });
              }}
            />
            <span>{t('accounts.messages.protection.accounts')}</span>
          </div>
        )}
        <Toggle
          label={t('accounts.messages.protection.skipInjured')}
          checked={protection.skip_injured}
          onChange={(value) => {
            set({ skip_injured: value });
          }}
        />
      </section>
      <section className="space-y-3">
        <h4 className="type-body-medium text-content-secondary">
          {t('accounts.messages.protection.humanTitle')}
        </h4>
        <Toggle
          label={t('accounts.messages.protection.typing', {
            min: protection.typing_min_seconds,
            max: protection.typing_max_seconds,
          })}
          checked={protection.typing}
          onChange={(value) => {
            set({ typing: value });
          }}
        />
      </section>
    </>
  );
}
