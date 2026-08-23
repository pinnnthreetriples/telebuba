import { useTranslation } from 'react-i18next';

import { Modal } from '@/shared/ui';

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
      className="w-[420px]"
      label={t('neurocomment.modal.campaignDelete.title', { name })}
    >
      <div className="p-6">
        <div className="mb-2 text-title font-bold">
          {t('neurocomment.modal.campaignDelete.title', { name })}
        </div>
        <div className="mb-[22px] text-lead leading-[1.5] text-ink-muted">
          {t('neurocomment.modal.campaignDelete.body')}
        </div>
        <div className="flex justify-end gap-sm">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-line-input bg-white px-[22px] py-[9px] text-lead font-semibold text-ink"
          >
            {t('neurocomment.modal.cancel')}
          </button>
          <button
            type="button"
            onClick={() => {
              onConfirm();
              onClose();
            }}
            className="rounded-full border border-danger-line bg-danger-tint px-[22px] py-[9px] text-lead font-semibold text-danger"
          >
            {t('neurocomment.modal.campaignDelete.confirm')}
          </button>
        </div>
      </div>
    </Modal>
  );
}
