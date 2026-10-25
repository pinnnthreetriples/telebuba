import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { createStandalonePhoneMutation, phonesQueryKey } from '@/entities/phone';
import { proxyPoolQueryOptions, ProxyPoolSelect } from '@/entities/proxy';
import { ProxyAddModal } from '@/widgets/account-edit';
import { Button, Input, Modal, ModalFooter, ModalHeader } from '@/shared/ui';

// A new independent emulator: a phone bound to no Telegram account, on a pool proxy
// picked here. A proxy missing from the pool is added the usual way, without leaving
// the dialog. The phone appears asleep; Start makes its pod.
export function StandaloneCreateModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const create = useMutation(createStandalonePhoneMutation());
  const [label, setLabel] = useState('');
  const [proxyId, setProxyId] = useState<string | null>(null);
  const [addingProxy, setAddingProxy] = useState(false);

  const onCreate = () => {
    create.mutate(
      { body: { label: label.trim() || null, proxy_id: proxyId } },
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
      <Modal size="form" label={title} onClose={onClose} dirty={label !== '' || proxyId !== null}>
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
