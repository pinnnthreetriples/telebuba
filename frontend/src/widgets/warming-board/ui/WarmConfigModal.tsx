import { useNavigate } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';

import { Modal } from '@/shared/ui';

// Warming behaviour/limits are one global settings row (WarmingSettingsUpdate
// has no account_id) — this modal used to duplicate the Settings page with a
// "Save" button that silently discarded every edit (both buttons just closed
// the modal). Rather than fake a per-account save the backend can't honor, it
// now says so plainly and links to the real Settings screen.
export function WarmConfigModal({ phone, onClose }: { phone: string; onClose: () => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  return (
    <Modal onClose={onClose} z={72} className="w-[420px]">
      <div className="p-6">
        <div className="mb-2 text-[16px] font-bold">{t('warming.cfg.title')}</div>
        <div className="mb-[22px] text-[13px] leading-[1.5] text-ink-muted">
          {t('warming.cfg.globalHint', { phone })}
        </div>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink"
          >
            {t('warming.cfg.close')}
          </button>
          <button
            type="button"
            onClick={() => {
              onClose();
              void navigate({ to: '/settings' });
            }}
            className="rounded-full bg-primary px-5 py-[9px] text-[13px] font-semibold text-white"
          >
            {t('warming.cfg.openSettings')}
          </button>
        </div>
      </div>
    </Modal>
  );
}
