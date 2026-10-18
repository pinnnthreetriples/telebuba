import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  androidVersion,
  PHONE_STATUS_TONE,
  phoneActions,
  PhoneMockup,
  phonesQueryKey,
  phonesQueryOptions,
  startPhoneMutation,
  stopPhoneMutation,
} from '@/entities/phone';
import { PhoneScreen, type ScreenMode } from '@/features/phone-screen';
import type { Phone } from '@/shared/api';
import { Badge, Button, EmptyState, Modal, ModalHeader, Notice, Spinner } from '@/shared/ui';

import { PhoneDetails } from './PhoneDetails';

// A phone's status changes on its own (a warming visit begins, a boot finishes):
// the list is polled while the page is open. Nothing here starts encoding — only
// an open screen does, and only while it is open.
const POLL_MS = 5000;

type Open = { phone: Phone; mode: ScreenMode } | { phone: Phone; mode: 'details' };

export function DevicesPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data, isPending, isError } = useQuery({
    ...phonesQueryOptions(),
    refetchInterval: POLL_MS,
  });
  const start = useMutation(startPhoneMutation());
  const stop = useMutation(stopPhoneMutation());
  const [open, setOpen] = useState<Open | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const act = (phone: Phone, action: 'start' | 'stop') => {
    const mutation = action === 'start' ? start : stop;
    setBusy(phone.phone_id);
    // The global mutation cache toasts a refusal; the catch only keeps it handled.
    void mutation
      .mutateAsync({ path: { phone_id: phone.phone_id } })
      .finally(() => {
        setBusy(null);
        void queryClient.invalidateQueries({ queryKey: phonesQueryKey() });
      })
      .catch(() => undefined);
  };

  const phones = data?.phones ?? [];
  return (
    <div className="tb-fadeup">
      <h1 className="m-0 mb-2 type-h1">{t('devices.title')}</h1>
      <p className="mb-6 type-body text-content-muted">{t('devices.subtitle')}</p>

      {data && !data.enabled && data.problem && (
        <div className="mb-4">
          <Notice tone="warning">
            {t(`devices.problem.${data.problem}`, { defaultValue: t('devices.problem.other') })}
          </Notice>
        </div>
      )}
      {isPending && <Spinner />}
      {isError && <EmptyState tone="danger">{t('devices.loadFailed')}</EmptyState>}
      {data && phones.length === 0 && <EmptyState boxed>{t('devices.empty')}</EmptyState>}

      <ul className="m-0 grid list-none grid-cols-2 gap-4 p-0 md:grid-cols-3 lg:grid-cols-4">
        {phones.map((phone) => {
          const can = phoneActions(phone.status);
          const app = phone.apps[0];
          const pending = busy === phone.phone_id;
          return (
            <li key={phone.phone_id} className="flex flex-col gap-2">
              <PhoneMockup brand={phone.maker}>
                <Badge tone={PHONE_STATUS_TONE[phone.status]} dot size="sm">
                  {t(`devices.status.${phone.status}`)}
                </Badge>
                <span className="type-body-medium">{phone.model_name}</span>
                <span className="type-small">{androidVersion(phone.system_version)}</span>
                {app && (
                  <span className="type-small truncate">
                    {t(`devices.platform.${app.platform}`)} ·{' '}
                    {app.username ? `@${app.username}` : (app.label ?? app.account_id)}
                  </span>
                )}
                {phone.proxy?.country_code && (
                  <span className="type-small">{phone.proxy.country_code}</span>
                )}
                {!phone.created && <span className="type-small">{t('devices.notCreated')}</span>}
              </PhoneMockup>
              <div className="flex flex-wrap gap-2">
                {can.start && (
                  <Button
                    size="sm"
                    variant="primary"
                    loading={pending}
                    disabled={!data?.enabled}
                    onClick={() => act(phone, 'start')}
                  >
                    {t('devices.action.start')}
                  </Button>
                )}
                {can.control && (
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={() => setOpen({ phone, mode: 'control' })}
                  >
                    {t('devices.action.control')}
                  </Button>
                )}
                {can.watch && (
                  <Button size="sm" onClick={() => setOpen({ phone, mode: 'view' })}>
                    {t('devices.action.watch')}
                  </Button>
                )}
                {can.stop && (
                  <Button size="sm" loading={pending} onClick={() => act(phone, 'stop')}>
                    {t('devices.action.stop')}
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setOpen({ phone, mode: 'details' })}
                >
                  {t('devices.action.details')}
                </Button>
              </div>
            </li>
          );
        })}
      </ul>

      {open && (
        <Modal
          size={open.mode === 'details' ? 'form' : 'panel'}
          label={open.phone.model_name}
          onClose={() => {
            setOpen(null);
            void queryClient.invalidateQueries({ queryKey: phonesQueryKey() });
          }}
        >
          <ModalHeader title={open.phone.model_name} subtitle={t(`devices.modal.${open.mode}`)} />
          <div className="p-6">
            {open.mode === 'details' ? (
              <PhoneDetails phone={open.phone} />
            ) : (
              <PhoneScreen phoneId={open.phone.phone_id} mode={open.mode} />
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}
