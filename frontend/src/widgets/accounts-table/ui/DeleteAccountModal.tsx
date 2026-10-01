import { dialogTitle, dialogBody } from '@/shared/design-system';
import { useTranslation } from 'react-i18next';

import { ModalFooter, ModalBody, Button, Modal } from '@/shared/ui';

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
    <Modal onClose={onClose} size="confirm" label={t('accounts.deleteModal.title', { phone })}>
      <ModalBody variant="form">
        <div className={dialogTitle()}>{t('accounts.deleteModal.title', { phone })}</div>
        <div className={dialogBody()}>{t('accounts.deleteModal.body')}</div>
        <ModalFooter variant="plain">
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
        </ModalFooter>
      </ModalBody>
    </Modal>
  );
}
