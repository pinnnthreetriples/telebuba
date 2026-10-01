import { dialogTitle, dialogBody } from '@/shared/design-system';
import { useTranslation } from 'react-i18next';

import { ModalFooter, ModalBody, Button, Modal } from '@/shared/ui';

// Design modal: campaign-delete (L1373-1385) — a destructive confirm.
export function CampaignDeleteModal({
  name,
  onClose,
  onConfirm,
}: {
  name: string;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Modal
      onClose={onClose}
      size="confirm"
      label={t('neurocomment.modal.campaignDelete.title', { name })}
    >
      <ModalBody variant="form">
        <div className={dialogTitle()}>
          {t('neurocomment.modal.campaignDelete.title', { name })}
        </div>
        <div className={dialogBody()}>{t('neurocomment.modal.campaignDelete.body')}</div>
        <ModalFooter variant="plain">
          <Button onClick={onClose}>{t('neurocomment.modal.cancel')}</Button>
          <Button
            variant="danger"
            onClick={() => {
              onConfirm();
              onClose();
            }}
          >
            {t('neurocomment.modal.campaignDelete.confirm')}
          </Button>
        </ModalFooter>
      </ModalBody>
    </Modal>
  );
}
