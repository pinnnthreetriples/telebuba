import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { createStandalonePhoneMutation, phonesQueryKey } from '@/entities/phone';
import { proxyPoolQueryOptions, ProxyPoolSelect } from '@/entities/proxy';
import { ProxyAddModal } from '@/widgets/account-edit';
import type { StandaloneEmulatorCreate } from '@/shared/api';
import { Button, Input, Modal, ModalFooter, ModalHeader } from '@/shared/ui';

type StandaloneApp = NonNullable<StandaloneEmulatorCreate['install_apps']>[number];

// The apps a start installs when the phone lacks them, in the order the dialog lists them.
const APPS: StandaloneApp[] = ['telegram', 'gosuslugi'];
const CHECKBOX = 'size-spinner shrink-0 accent-action-primary';

// A new independent emulator: a phone bound to no Telegram account, on a pool proxy
// picked here. A proxy missing from the pool is added the usual way, without leaving
// the dialog. The operator ticks the apps its start installs (Telegram by default,
// Госуслуги on request). The phone appears asleep; Start makes its pod.
export function StandaloneCreateModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const create = useMutation(createStandalonePhoneMutation());
  const [label, setLabel] = useState('');
  const [proxyId, setProxyId] = useState<string | null>(null);
  const [addingProxy, setAddingProxy] = useState(false);
  const [apps, setApps] = useState<StandaloneApp[]>(['telegram']);

  const toggleApp = (app: StandaloneApp) => {
    setApps((current) =>
      current.includes(app)
        ? current.filter((a) => a !== app)
        : APPS.filter((a) => a === app || current.includes(a)),
    );
  };

  const onCreate = () => {
    create.mutate(
      { body: { label: label.trim() || null, proxy_id: proxyId, install_apps: apps } },
      {
        onSuccess: () => {
          void queryClient.invalidateQueries({ queryKey: phonesQueryKey() });
          void queryClient.invalidateQueries({ queryKey: proxyPoolQueryOptions().queryKey });
          onClose();
        },
      },
    );
  };

  const title = t('devices.standalone.createTitle');
  return (
    <>
      <Modal
        size="form"
        label={title}
        onClose={onClose}
        dirty={label !== '' || proxyId !== null || apps.join() !== 'telegram'}
      >
        {(close) => (
          <>
            <ModalHeader title={title} subtitle={t('devices.standalone.createSubtitle')} />
            <div className="flex flex-col gap-4 p-6">
              <label className="flex flex-col gap-1">
                <span className="type-small">{t('devices.standalone.label')}</span>
                <Input
                  value={label}
                  maxLength={64}
                  placeholder={t('devices.standalone.labelPlaceholder')}
                  onChange={(event) => setLabel(event.target.value)}
                />
              </label>
              <div className="flex flex-col gap-1">
                <span className="type-small">{t('devices.details.proxy')}</span>
                <ProxyPoolSelect
                  value={proxyId}
                  onChange={setProxyId}
                  emptyLabel={t('devices.standalone.noFreeProxy')}
                />
                <div>
                  <Button size="sm" variant="ghost" onClick={() => setAddingProxy(true)}>
                    {t('devices.standalone.addProxy')}
                  </Button>
                </div>
              </div>
              <fieldset className="flex flex-col gap-2">
                <legend className="type-small mb-1">{t('devices.standalone.apps')}</legend>
                {APPS.map((app) => (
                  <label key={app} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={apps.includes(app)}
                      onChange={() => toggleApp(app)}
                      className={CHECKBOX}
                    />
                    <span>{t(`devices.standalone.app.${app}`)}</span>
                  </label>
                ))}
                <span className="type-small text-content-subtle">
                  {t('devices.standalone.appsHint')}
                </span>
              </fieldset>
            </div>
            <ModalFooter>
              <Button onClick={close}>{t('devices.standalone.cancel')}</Button>
              <Button variant="primary" loading={create.isPending} onClick={onCreate}>
                {t('devices.standalone.create')}
              </Button>
            </ModalFooter>
          </>
        )}
      </Modal>
      {addingProxy && <ProxyAddModal onClose={() => setAddingProxy(false)} />}
    </>
  );
}
