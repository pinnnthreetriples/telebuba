import { useTranslation } from 'react-i18next';

import type { ChannelLanguageShortage } from '@/shared/api';
import { Notice } from '@/shared/ui';

// One line per phone language whose accounts have too few channels of their own, so
// the operator knows which pool to grow: "для es: 2 канала на 7 аккаунтов".
export function ChannelLanguageShortages({ shortages }: { shortages: ChannelLanguageShortage[] }) {
  const { t } = useTranslation();
  if (!shortages.length) return null;
  return (
    <div className="mb-3">
      <Notice tone="warning">
        <ul>
          {shortages.map((item) => (
            <li key={item.language}>
              {t('warming.channels.shortage', {
                language: item.language,
                channels: t('warming.channels.channelCount', { count: item.channels }),
                accounts: t('warming.channels.accountCount', { count: item.accounts }),
              })}
            </li>
          ))}
        </ul>
      </Notice>
    </div>
  );
}
