import { useTranslation } from 'react-i18next';

import { Modal } from '@/shared/ui';

import { ProxyForm } from './ProxyForm';

// The design's add-proxy modal: a single white card with the shared proxy form
// and Add/Cancel. ponytail: design-first — confirm just closes, no backend call.
export function ProxyAddModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
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
        <ProxyForm />
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
            onClick={onClose}
            className="rounded-full bg-primary px-5 py-[9px] text-[13px] font-medium text-white"
          >
            {t('accounts.proxyAdd.add')}
          </button>
        </div>
      </div>
    </Modal>
  );
}
