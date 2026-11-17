import { useTranslation } from 'react-i18next';

import { Notice } from '@/shared/ui';

import type { SpamCheckResult } from '@/shared/api';
import { formatLocalDateTime } from '@/shared/lib';

import { instantMs } from '../model/spamCard';

// The answer of a Spam-check that did not ask @SpamBot: the budget postponed it, refused it, or
// handed back the last reading. Never an empty answer: it names the reason and, when the budget
// knows it, the moment the account can be asked again.
export function SpamCheckNotice({ result }: { result: SpamCheckResult }) {
  const { t, i18n } = useTranslation();
  const retryMs = instantMs(result.retry_at ?? result.next_allowed_at);
  const text =
    result.served_from === 'coalesced'
      ? t('accounts.edit.spamCard.held.coalesced')
      : t(`accounts.edit.spamCard.held.reason.${result.reason ?? 'unknown'}`, {
          defaultValue: t('accounts.edit.spamCard.held.reason.unknown'),
        });
  return (
    <div className="mb-3">
      <Notice tone="warning" role="status">
        <p className="m-0 type-body-medium">{t('accounts.edit.spamCard.held.title')}</p>
        <p className="m-0 type-small">{text}</p>
        {retryMs !== null && (
          <p className="m-0 type-small">
            {t('accounts.edit.spamCard.held.retry', {
              when: formatLocalDateTime(retryMs, i18n.language),
            })}
          </p>
        )}
      </Notice>
    </div>
  );
}
