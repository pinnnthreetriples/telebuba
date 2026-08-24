import { useTranslation } from 'react-i18next';

import { Button, Modal } from '@/shared/ui';

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
    <Modal
      onClose={onClose}
      className="w-confirm"
      label={t('accounts.proxyDeleteModal.title', { endpoint })}
    >
      <div className="p-2xl">
        <div className="mb-sm text-title font-bold">
          {t('accounts.proxyDeleteModal.title', { endpoint })}
        </div>
        <div className="mb-2xl text-lead leading-[1.5] text-ink-muted">
          {used > 0
            ? t('accounts.proxyDeleteModal.bodyAssigned', { count: used })
            : t('accounts.proxyDeleteModal.body')}
        </div>
        <div className="flex justify-end gap-sm">
          <Button onClick={onClose}>{t('accounts.proxyDeleteModal.cancel')}</Button>
          <Button
            variant="danger"
            onClick={() => {
              onConfirm();
              onClose();
            }}
          >
            {t('accounts.proxyDeleteModal.confirm')}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
