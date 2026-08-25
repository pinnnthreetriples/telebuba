import { useTranslation } from 'react-i18next';

import { Button, Icon, Modal } from '@/shared/ui';

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
    <Modal onClose={onClose} className="w-form" label={t('warming.stopModal.title')}>
      <div className="p-2xl">
        <div className="mb-sm text-title font-bold">{t('warming.stopModal.title')}</div>
        <div className="mb-2xl text-lead leading-[1.5] text-ink-muted">
          {t('warming.stopModal.body', { phone })}
        </div>
        <div className="flex gap-sm">
          <Button
            variant="primary"
            className="flex-1"
            onClick={() => {
              onFinish();
              onClose();
            }}
          >
            <Icon name="check" size={14} />
            {t('warming.stopModal.toWarmed')}
          </Button>
          <Button className="flex-1 hover:bg-surface" onClick={onClose}>
            {t('warming.stopModal.keep')}
          </Button>
          {/* The one destructive button in the app that is white at rest and tints
              only under the pointer, rather than `danger`'s always-tinted box: it
              stands between two calm buttons in a three-way choice, and a filled
              third would read as the recommended one. The variant supplies the
              border, the ink and the states; the resting fill is the override. */}
          <Button
            variant="danger"
            className="flex-1 bg-white hover:border-danger-line hover:bg-danger-tint"
            onClick={() => {
              onStop();
              onClose();
            }}
          >
            {t('warming.stopModal.stop')}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
