import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  androidVersion,
  formatOtpCountdown,
  isLiveTelephonyStatus,
  isTelephonyLineStatus,
  phoneTelephonyStatusOptions,
  phoneTelephonyStatusQueryKey,
  startPhoneOtpWaitMutation,
  TELEPHONY_STATUS_TONE,
  type TelephonyLineStatus,
} from '@/entities/phone';
import type { Phone } from '@/shared/api';
import { Badge, Button } from '@/shared/ui';

import { SmsInboxDrawer } from './SmsInboxDrawer';
import { StandaloneSection } from './StandaloneSection';

const STATUS_POLL_MS = 2000;

// What the phone presents, where its traffic goes, and its cellular/SMS OTP line state.
// The line is polled only while it can change on its own (waiting, code received,
// typing); an idle or ended line changes when the operator acts, which refetches it.
// An independent emulator carries no account, so it has no SMS line; it shows its
// proxy picker and removal instead.
export function PhoneDetails({ phone, onDeleted }: { phone: Phone; onDeleted?: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [inboxOpen, setInboxOpen] = useState(false);
  const standalone = phone.kind === 'standalone';
  const statusOptions = phoneTelephonyStatusOptions({ path: { phone_id: phone.phone_id } });
  const { data: telephonyStatus } = useQuery({
    ...statusOptions,
    enabled: !standalone,
    refetchInterval: (query) =>
      isLiveTelephonyStatus(query.state.data?.status) ? STATUS_POLL_MS : false,
  });
  const startWait = useMutation(startPhoneOtpWaitMutation());

  const rawStatus = telephonyStatus?.status;
  const lineStatus: TelephonyLineStatus = isTelephonyLineStatus(rawStatus) ? rawStatus : 'idle';
  const lastCode = telephonyStatus?.last_code ?? null;
  const remainingSeconds = telephonyStatus?.remaining_seconds ?? 0;
  const errorCode = telephonyStatus?.error_code ?? null;

  const waitForSms = () => {
    void startWait
      .mutateAsync({ path: { phone_id: phone.phone_id } })
      .then((status) => {
        queryClient.setQueryData(statusOptions.queryKey, status);
      })
      // The global mutation cache toasts a refusal; the catch only keeps it handled.
      .catch(() => undefined)
      .finally(() => {
        void queryClient.invalidateQueries({
          queryKey: phoneTelephonyStatusQueryKey({ path: { phone_id: phone.phone_id } }),
        });
      });
  };

  const renderStatusLabel = () => {
    switch (lineStatus) {
      case 'waiting_otp':
        return remainingSeconds > 0
          ? t('telephony.status.waitingOtpTimer', {
              timer: formatOtpCountdown(remainingSeconds),
            })
          : t('telephony.status.waitingOtp');
      case 'otp_received':
        return lastCode
          ? t('telephony.status.codeReceived', { code: lastCode })
          : t('telephony.status.waitingOtp');
      case 'typing':
        return lastCode
          ? t('telephony.status.codeReceivedTyping', { code: lastCode })
          : t('telephony.status.typing');
      case 'completed':
        return t('telephony.status.completed');
      case 'failed':
        return errorCode
          ? t('telephony.status.failedWith', {
              reason: t(`shell.code.${errorCode}`, { defaultValue: errorCode }),
            })
          : t('telephony.status.failed');
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
          {phone.apps.length === 0 && '—'}
          {phone.apps.map((app) => (
            <div key={`${app.platform}:${app.account_id}`}>
              {t(`devices.platform.${app.platform}`)} · {app.label ?? app.account_id}
            </div>
          ))}
        </dd>
      </dl>

      {standalone ? (
        <StandaloneSection phone={phone} onDeleted={onDeleted ?? (() => undefined)} />
      ) : (
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
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="primary"
                disabled={isLiveTelephonyStatus(lineStatus)}
                loading={startWait.isPending}
                onClick={waitForSms}
              >
                {t('telephony.waitSms')}
              </Button>
              <Button size="sm" variant="secondary" onClick={() => setInboxOpen(true)}>
                {t('telephony.inbox.title')}
              </Button>
            </div>
          </div>
        </div>
      )}

      {inboxOpen ? (
        <SmsInboxDrawer phoneId={phone.phone_id} onClose={() => setInboxOpen(false)} />
      ) : null}
    </div>
  );
}
