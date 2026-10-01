import { useTranslation } from 'react-i18next';

import { SectionStack, Input } from '@/shared/ui';

import type { AccountRead } from '@/shared/api';

import { Section } from './_shared';
import { LABEL } from './_styles';

// Device fingerprint card: three immutable, locked fields (the profile is created
// at registration and never mutated — non-negotiable #9).
export function DeviceSection({ account }: { account: AccountRead }) {
  const { t } = useTranslation();
  return (
    <Section
      title={t('accounts.edit.device')}
      icon={
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className="text-content-subtle"
        >
          <rect x="3" y="11" width="18" height="11" rx="2" />
          <path d="M7 11V7a5 5 0 0 1 10 0v4" />
        </svg>
      }
    >
      <div className="mb-lg type-prose">{t('accounts.edit.deviceLocked')}</div>
      <SectionStack gap="compact">
        <label>
          <span className={LABEL}>{t('accounts.edit.deviceModel')}</span>
          <Input
            value={account.device_model ?? '—'}
            disabled
            tone="inert"
            className="cursor-not-allowed"
          />
        </label>
        <label>
          <span className={LABEL}>{t('accounts.edit.deviceOs')}</span>
          <Input
            value={account.device_system_version ?? '—'}
            disabled
            tone="inert"
            className="cursor-not-allowed"
          />
        </label>
        <label>
          <span className={LABEL}>{t('accounts.edit.deviceLang')}</span>
          <Input
            value={account.device_lang ?? '—'}
            disabled
            tone="inert"
            className="cursor-not-allowed"
          />
        </label>
      </SectionStack>
    </Section>
  );
}
