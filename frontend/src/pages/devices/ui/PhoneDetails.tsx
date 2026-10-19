import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  androidVersion,
  formatOtpCountdown,
  isTelephonyLineStatus,
  phoneTelephonyStatusOptions,
  TELEPHONY_STATUS_TONE,
  type TelephonyLineStatus,
} from '@/entities/phone';
import type { Phone } from '@/shared/api';
import { Badge, Button } from '@/shared/ui';

import { SmsInboxDrawer } from './SmsInboxDrawer';

const STATUS_POLL_MS = 2000;

// What the phone presents, where its traffic goes, and its cellular/SMS OTP line state.
export function PhoneDetails({ phone }: { phone: Phone }) {
  const { t } = useTranslation();
  const [inboxOpen, setInboxOpen] = useState(false);
  const { data: telephonyStatus } = useQuery({
    ...phoneTelephonyStatusOptions({ path: { phone_id: phone.phone_id } }),
    refetchInterval: STATUS_POLL_MS,
  });

  const lineStatus: TelephonyLineStatus = isTelephonyLineStatus(telephonyStatus?.status)
    ? telephonyStatus.status
    : 'idle';
  const lastCode = telephonyStatus?.last_code ?? null;
  const remainingSeconds = telephonyStatus?.remaining_seconds ?? 0;

  const renderStatusLabel = () => {
    switch (lineStatus) {
      case 'waiting_otp':
        return remainingSeconds > 0
          ? t('telephony.status.waitingOtpTimer', {
              timer: formatOtpCountdown(remainingSeconds),
            })
          : t('telephony.status.waitingOtp');
      case 'otp_received':
      case 'typing':
        return lastCode
          ? t('telephony.status.codeReceivedTyping', { code: lastCode })
          : t('telephony.status.typing');
      case 'completed':
        return t('telephony.status.completed');
      case 'timeout':
        return t('telephony.status.timeout');
      case 'idle':
      default:
        return t('telephony.status.idle');
    }
  };

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
    <div className="flex flex-col gap-4">
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

      <div className="flex flex-col gap-2 border-t border-canvas pt-4">
        <span className="type-body-medium">{t('telephony.sectionTitle')}</span>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Badge
            tone={TELEPHONY_STATUS_TONE[lineStatus]}
            dot={lineStatus !== 'idle'}
            size="sm"
            className={lineStatus === 'waiting_otp' ? 'tb-pulse' : undefined}
          >
            {renderStatusLabel()}
          </Badge>
          <Button size="sm" variant="secondary" onClick={() => setInboxOpen(true)}>
            {t('telephony.inbox.title')}
          </Button>
        </div>
      </div>

      {inboxOpen ? (
        <SmsInboxDrawer phoneId={phone.phone_id} onClose={() => setInboxOpen(false)} />
      ) : null}
    </div>
  );
}