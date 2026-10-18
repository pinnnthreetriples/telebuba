import { useTranslation } from 'react-i18next';

import { androidVersion } from '@/entities/phone';
import type { Phone } from '@/shared/api';

// What the phone presents and where its traffic goes. The proxy is shown by its
// address only: the API never sends its credentials.
export function PhoneDetails({ phone }: { phone: Phone }) {
  const { t } = useTranslation();
  const proxy = phone.proxy;
  const rows: [string, string][] = [
    [t('devices.details.model'), `${phone.maker} ${phone.model_name}`.trim()],
    [t('devices.details.telegramModel'), phone.device_model],
    [
      t('devices.details.system'),
      `${androidVersion(phone.system_version)} (${phone.system_version})`,
    ],
    [t('devices.details.app'), phone.app_version],
    [t('devices.details.lang'), phone.lang_code],
    [
      t('devices.details.proxy'),
      proxy?.host
        ? `${proxy.proxy_type ?? ''} ${proxy.host}:${proxy.port ?? ''}`.trim()
        : t('devices.details.noProxy'),
    ],
    [t('devices.details.country'), proxy?.country_code ?? '—'],
    [t('devices.details.container'), phone.phone_id],
  ];
  return (
    <dl className="m-0 grid grid-cols-2 gap-x-4 gap-y-2">
      {rows.map(([term, value]) => (
        <div key={term} className="contents">
          <dt className="type-small">{term}</dt>
          <dd className="m-0 type-body break-all">{value}</dd>
        </div>
      ))}
      <dt className="type-small">{t('devices.details.apps')}</dt>
      <dd className="m-0 type-body">
        {phone.apps.map((app) => (
          <div key={`${app.platform}:${app.account_id}`}>
            {t(`devices.platform.${app.platform}`)} · {app.label ?? app.account_id}
          </div>
        ))}
      </dd>
    </dl>
  );
}
