import { useTranslation } from 'react-i18next';

import { Button, Modal } from '@/shared/ui';

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
      <div className="p-2xl">
        <div className="mb-sm type-dialog-title">
          {t('neurocomment.modal.campaignDelete.title', { name })}
        </div>
        <div className="mb-2xl type-dialog-body">{t('neurocomment.modal.campaignDelete.body')}</div>
        <div className="flex justify-end gap-sm">
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
        </div>
      </div>
    </Modal>
  );
}
