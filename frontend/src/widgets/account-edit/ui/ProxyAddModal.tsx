import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { createProxyMutation, proxyPoolQueryOptions } from '@/entities/proxy';
import { Button, IconButton, Modal } from '@/shared/ui';

import { ProxyForm } from './ProxyForm';
import { EMPTY_PROXY_FORM, type ProxyFormValue } from './proxyFormValue';

// The design's add-proxy modal: the shared proxy form + Add/Cancel. Add creates
// a real pool proxy (POST /proxies) and refreshes the pool.
export function ProxyAddModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [value, setValue] = useState<ProxyFormValue>(EMPTY_PROXY_FORM);
  const [valid, setValid] = useState(false);
  const create = useMutation(createProxyMutation());
  const canAdd = valid && !create.isPending;

  const onAdd = () => {
    create.mutate(
      {
        body: {
          proxy_type: value.proxy_type,
          host: value.host.trim(),
          port: Number(value.port),
          username: value.username.trim() || null,
          password: value.password || null,
        },
      },
      {
        onSuccess: () => {
          // A brand-new pool proxy is assigned to nobody yet, so only the pool
          // changed — not the accounts table, the stat tiles, or anything else
          // a bare invalidateQueries() was refetching.
          void queryClient.invalidateQueries({ queryKey: proxyPoolQueryOptions().queryKey });
          onClose();
        },
      },
    );
  };

  return (
    <Modal onClose={onClose} size="form" label={t('accounts.proxyAdd.title')}>
      <div className="p-2xl">
        <div className="mb-lg flex items-center justify-between">
          <span className="type-dialog-title">{t('accounts.proxyAdd.title')}</span>
          <IconButton
            size="md"
            onClick={onClose}
            aria-label={t('accounts.proxyAdd.close')}
            className="text-title"
          >
            ×
          </IconButton>
        </div>
        <ProxyForm value={value} onChange={setValue} onValidityChange={setValid} />
        <div className="mt-xl flex justify-end gap-sm">
          <Button onClick={onClose}>{t('accounts.proxyAdd.cancel')}</Button>
          <Button variant="primary" onClick={onAdd} disabled={!canAdd}>
            {t('accounts.proxyAdd.add')}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
