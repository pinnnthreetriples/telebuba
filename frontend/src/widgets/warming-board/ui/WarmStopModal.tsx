import { useTranslation } from 'react-i18next';

import { Modal } from '@/shared/ui';

// The design's "stop warming?" confirm (three actions: finish→warmed, keep
// going, hard stop).
export function WarmStopModal({
  phone,
  onClose,
  onStop,
  onFinish,
}: {
  phone: string;
  onClose: () => void;
  onStop: () => void;
  onFinish: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Modal onClose={onClose} z={72} className="w-[420px]">
      <div className="p-6">
        <div className="mb-2 text-[16px] font-bold">{t('warming.stopModal.title')}</div>
        <div className="mb-[22px] text-[13px] leading-[1.5] text-ink-muted">
          {t('warming.stopModal.body', { phone })}
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => {
              onFinish();
              onClose();
            }}
            className="flex flex-1 items-center justify-center gap-[5px] whitespace-nowrap rounded-full border border-primary bg-primary px-3 py-[9px] text-[13px] font-semibold text-white transition-colors hover:bg-[#0057db]"
          >
            <svg
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="#fff"
              strokeWidth="2.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M20 6 9 17l-5-5" />
            </svg>
            {t('warming.stopModal.toWarmed')}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="flex flex-1 items-center justify-center whitespace-nowrap rounded-full border border-line-input bg-white px-3 py-[9px] text-[13px] font-medium text-ink transition-colors hover:border-[#c8c6c2] hover:bg-[#f7f6f4]"
          >
            {t('warming.stopModal.keep')}
          </button>
          <button
            type="button"
            onClick={() => {
              onStop();
              onClose();
            }}
            className="flex flex-1 items-center justify-center whitespace-nowrap rounded-full border border-[#e6cfcd] bg-white px-3 py-[9px] text-[13px] font-semibold text-danger transition-colors hover:border-[#e0b6b2] hover:bg-danger-tint"
          >
            {t('warming.stopModal.stop')}
          </button>
        </div>
      </div>
    </Modal>
  );
}
