import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { createProxyMutation } from '@/entities/proxy';
import { Modal } from '@/shared/ui';

import { ProxyForm } from './ProxyForm';
import { EMPTY_PROXY_FORM, type ProxyFormValue } from './proxyFormValue';

// The design's add-proxy modal: the shared proxy form + Add/Cancel. Add creates
// a real pool proxy (POST /proxies) and refreshes the pool.
export function ProxyAddModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [value, setValue] = useState<ProxyFormValue>(EMPTY_PROXY_FORM);
  const create = useMutation(createProxyMutation());
  const canAdd = value.host.trim() !== '' && value.port !== '' && !create.isPending;

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
          void queryClient.invalidateQueries();
          onClose();
        },
      },
    );
  };

  return (
    <Modal onClose={onClose} z={70} className="w-[460px]">
      <div className="p-6">
        <div className="mb-4 flex items-center justify-between">
          <span className="text-[16px] font-bold">{t('accounts.proxyAdd.title')}</span>
          <button
            type="button"
            onClick={onClose}
            aria-label={t('accounts.proxyAdd.close')}
            className="h-[30px] w-[30px] rounded-full border border-line bg-white text-[16px] text-ink-muted"
          >
            ×
          </button>
        </div>
        <ProxyForm value={value} onChange={setValue} />
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink"
          >
            {t('accounts.proxyAdd.cancel')}
          </button>
          <button
            type="button"
            onClick={onAdd}
            disabled={!canAdd}
            className="rounded-full bg-primary px-5 py-[9px] text-[13px] font-medium text-white disabled:opacity-50"
          >
            {t('accounts.proxyAdd.add')}
          </button>
        </div>
      </div>
    </Modal>
  );
}
