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
    <Modal onClose={onClose} className="w-[468px]" label={t('warming.stopModal.title')}>
      <div className="p-2xl">
        <div className="mb-sm text-title font-bold">{t('warming.stopModal.title')}</div>
        <div className="mb-2xl text-lead leading-[1.5] text-ink-muted">
          {t('warming.stopModal.body', { phone })}
        </div>
        <div className="flex gap-sm">
          <button
            type="button"
            onClick={() => {
              onFinish();
              onClose();
            }}
            className="flex flex-1 items-center justify-center gap-tight whitespace-nowrap rounded-full border border-primary bg-primary px-md py-md text-lead font-semibold text-white transition-colors hover:bg-primary-press"
          >
            <svg
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
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
            className="flex flex-1 items-center justify-center whitespace-nowrap rounded-full border border-line-input bg-white px-md py-md text-lead font-semibold text-ink transition-colors hover:border-line-strong hover:bg-surface"
          >
            {t('warming.stopModal.keep')}
          </button>
          <button
            type="button"
            onClick={() => {
              onStop();
              onClose();
            }}
            className="flex flex-1 items-center justify-center whitespace-nowrap rounded-full border border-danger-line bg-white px-md py-md text-lead font-semibold text-danger-deep transition-colors hover:border-danger-line hover:bg-danger-tint"
          >
            {t('warming.stopModal.stop')}
          </button>
        </div>
      </div>
    </Modal>
  );
}
