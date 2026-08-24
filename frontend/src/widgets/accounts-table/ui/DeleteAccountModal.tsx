import { useTranslation } from 'react-i18next';

import { Button, Modal } from '@/shared/ui';

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
        <div className="mb-2 text-title font-bold">
          {t('accounts.deleteModal.title', { phone })}
        </div>
        <div className="mb-[22px] text-lead leading-[1.5] text-ink-muted">
          {t('accounts.deleteModal.body')}
        </div>
        <div className="flex justify-end gap-sm">
          <Button onClick={onClose}>{t('accounts.deleteModal.cancel')}</Button>
          <Button
            variant="danger"
            onClick={() => {
              onConfirm();
              onClose();
            }}
          >
            {t('accounts.deleteModal.confirm')}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
