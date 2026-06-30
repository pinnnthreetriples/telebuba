import { useTranslation } from 'react-i18next';

import { Modal } from '@/shared/ui';

// Confirm dialog for deleting a pool proxy (the card's × button). Warns when the
// proxy still serves accounts — deleting it detaches them (their proxy is cleared).
export function ProxyDeleteModal({
  endpoint,
  used,
  onClose,
  onConfirm,
}: {
  endpoint: string;
  used: number;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Modal onClose={onClose} z={70} className="w-[420px]">
      <div className="p-6">
        <div className="mb-2 text-[16px] font-bold">
          {t('accounts.proxyDeleteModal.title', { endpoint })}
        </div>
        <div className="mb-[22px] text-[13px] leading-[1.5] text-ink-muted">
          {used > 0
            ? t('accounts.proxyDeleteModal.bodyAssigned', { count: used })
            : t('accounts.proxyDeleteModal.body')}
        </div>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink"
          >
            {t('accounts.proxyDeleteModal.cancel')}
          </button>
          <button
            type="button"
            onClick={() => {
              onConfirm();
              onClose();
            }}
            className="rounded-full border border-[#f0c9c5] bg-danger-tint px-5 py-[9px] text-[13px] font-semibold text-danger"
          >
            {t('accounts.proxyDeleteModal.confirm')}
          </button>
        </div>
      </div>
    </Modal>
  );
}
