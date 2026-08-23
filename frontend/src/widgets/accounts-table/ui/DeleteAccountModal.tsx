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
    <Modal
      onClose={onClose}
      className="w-[420px]"
      label={t('accounts.deleteModal.title', { phone })}
    >
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
            className="rounded-full border border-line-input bg-white px-[22px] py-[9px] text-[13px] font-semibold text-ink"
          >
            {t('accounts.deleteModal.cancel')}
          </button>
          <button
            type="button"
            onClick={() => {
              onConfirm();
              onClose();
            }}
            className="rounded-full border border-danger-line bg-danger-tint px-[22px] py-[9px] text-[13px] font-semibold text-danger"
          >
            {t('accounts.deleteModal.confirm')}
          </button>
        </div>
      </div>
    </Modal>
  );
}
