import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { createProxyMutation, proxyPoolQueryOptions } from '@/entities/proxy';
import { IconButton, Modal } from '@/shared/ui';

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
    <Modal onClose={onClose} className="w-[460px]" label={t('accounts.proxyAdd.title')}>
      <div className="p-6">
        <div className="mb-4 flex items-center justify-between">
          <span className="text-[16px] font-bold">{t('accounts.proxyAdd.title')}</span>
          <IconButton
            size="md"
            onClick={onClose}
            aria-label={t('accounts.proxyAdd.close')}
            className="text-[16px]"
          >
            ×
          </IconButton>
        </div>
        <ProxyForm value={value} onChange={setValue} onValidityChange={setValid} />
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-line-input bg-white px-[22px] py-[9px] text-[13px] font-semibold text-ink"
          >
            {t('accounts.proxyAdd.cancel')}
          </button>
          <button
            type="button"
            onClick={onAdd}
            disabled={!canAdd}
            className="rounded-full bg-primary px-[22px] py-[9px] text-[13px] font-semibold text-white disabled:opacity-50"
          >
            {t('accounts.proxyAdd.add')}
          </button>
        </div>
      </div>
    </Modal>
  );
}
