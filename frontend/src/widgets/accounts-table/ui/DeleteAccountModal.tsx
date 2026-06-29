import { useTranslation } from 'react-i18next';

import { Modal } from '@/shared/ui';

// The design's delete-account confirm dialog.
export function DeleteAccountModal({
  phone,
  onClose,
  onConfirm,
}: {
  phone: string;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Modal onClose={onClose} z={70} className="w-[420px]">
      <div className="p-6">
        <div className="mb-2 text-[16px] font-bold">
          {t('accounts.deleteModal.title', { phone })}
        </div>
        <div className="mb-[22px] text-[13px] leading-[1.5] text-ink-muted">
          {t('accounts.deleteModal.body')}
        </div>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink"
          >
            {t('accounts.deleteModal.cancel')}
          </button>
          <button
            type="button"
            onClick={() => {
              onConfirm();
              onClose();
            }}
            className="rounded-full border border-[#f0c9c5] bg-danger-tint px-5 py-[9px] text-[13px] font-semibold text-danger"
          >
            {t('accounts.deleteModal.confirm')}
          </button>
        </div>
      </div>
    </Modal>
  );
}
