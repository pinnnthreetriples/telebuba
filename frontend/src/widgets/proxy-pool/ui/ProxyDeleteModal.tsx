import { dialogTitle, dialogBody } from '@/shared/design-system';
import { useTranslation } from 'react-i18next';

import { ModalFooter, ModalBody, Button, Modal } from '@/shared/ui';

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
      size="confirm"
      label={t('accounts.proxyDeleteModal.title', { endpoint })}
    >
      <ModalBody variant="form">
        <div className={dialogTitle()}>{t('accounts.proxyDeleteModal.title', { endpoint })}</div>
        <div className={dialogBody()}>
          {used > 0
            ? t('accounts.proxyDeleteModal.bodyAssigned', { count: used })
            : t('accounts.proxyDeleteModal.body')}
        </div>
        <ModalFooter variant="plain">
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
        </ModalFooter>
      </ModalBody>
    </Modal>
  );
}
