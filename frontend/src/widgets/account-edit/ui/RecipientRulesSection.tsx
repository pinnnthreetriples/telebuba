import { useTranslation } from 'react-i18next';

import { Switch } from '@/shared/ui';

import { DEFAULT_RECIPIENT_RULES, type FullRecipientRules } from '../model/recipientRules';
import { NumberField } from './ScheduleSection';

const MAX_NO_REPEAT_DAYS = 365;

// "Who and how often": no repeat within N days, one client — one account, and a
// next-day re-check of "not found" numbers by another account.
export function RecipientRulesSection({
  rules,
  onChange,
}: {
  rules: FullRecipientRules;
  onChange: (rules: FullRecipientRules) => void;
}) {
  const { t } = useTranslation();
  const set = (patch: Partial<FullRecipientRules>) => {
    onChange({ ...rules, ...patch });
  };

  return (
    <section className="space-y-3">
      <h4 className="type-body-medium text-content-secondary">
        {t('accounts.messages.recipientRules.title')}
      </h4>
      <div className="flex flex-wrap items-center justify-between gap-3 type-small">
        <div className="flex flex-wrap items-center gap-2">
          <span>{t('accounts.messages.recipientRules.noRepeat')}</span>
          {rules.no_repeat_days !== null && (
            <>
              <span>{t('accounts.messages.recipientRules.within')}</span>
              <NumberField
                label={t('accounts.messages.recipientRules.noRepeatDays')}
                value={rules.no_repeat_days}
                min={1}
                max={MAX_NO_REPEAT_DAYS}
                onChange={(value) => {
                  set({ no_repeat_days: value });
                }}
              />
              <span>{t('accounts.messages.recipientRules.days')}</span>
            </>
          )}
        </div>
        <Switch
          checked={rules.no_repeat_days !== null}
          onChange={(value) => {
            set({ no_repeat_days: value ? DEFAULT_RECIPIENT_RULES.no_repeat_days : null });
          }}
          label={t('accounts.messages.recipientRules.noRepeat')}
        />
      </div>
      <label className="flex items-center justify-between gap-3 type-small">
        <span>{t('accounts.messages.recipientRules.oneAccount')}</span>
        <Switch
          checked={rules.one_account_per_client}
          onChange={(value) => {
            set({ one_account_per_client: value });
          }}
          label={t('accounts.messages.recipientRules.oneAccount')}
        />
      </label>
      <label className="flex items-center justify-between gap-3 type-small">
        <span>{t('accounts.messages.recipientRules.recheck')}</span>
        <Switch
          checked={rules.recheck_not_found}
          onChange={(value) => {
            set({ recheck_not_found: value });
          }}
          label={t('accounts.messages.recipientRules.recheck')}
        />
      </label>
    </section>
  );
}
