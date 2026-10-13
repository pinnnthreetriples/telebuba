import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { SoftStartPlan } from '@/shared/api';
import { Button, Modal } from '@/shared/ui';

export type HandoffMode = 'soft' | 'normal';

// The soft start's numbers until the board answers; the board's `soft_start` is the truth.
const FALLBACK_PLAN: Required<SoftStartPlan> = {
  days: 5,
  early_days: 2,
  early_comments_per_hour: 2,
  early_comments_per_channel_per_day: 1,
  early_joins_per_day: 5,
  late_comments_per_hour: 4,
  late_comments_per_channel_per_day: 2,
  late_joins_per_day: 10,
  first_day_comments: 5,
  last_day_comments: 15,
};

// «В нейрокомментинг»: how the account enters neurocomment. The soft start (lowered
// limits for its first days) is preselected; «Как обычно» takes the usual limits at once.
// The @SpamBot check before the hand-off stays the page's: an unanswered check still
// opens only «Повторить», with no way around it.
export function HandoffModal({
  plan,
  onClose,
  onConfirm,
}: {
  plan?: SoftStartPlan;
  onClose: () => void;
  onConfirm: (mode: HandoffMode) => void;
}) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<HandoffMode>('soft');
  const soft = { ...FALLBACK_PLAN, ...plan };
  const options: { mode: HandoffMode; name: string; detail: string }[] = [
    {
      mode: 'soft',
      name: t('warming.handoff.softName'),
      detail: t('warming.handoff.softDetail', {
        days: soft.days,
        earlyDays: soft.early_days,
        earlyHour: soft.early_comments_per_hour,
        earlyChannel: soft.early_comments_per_channel_per_day,
        earlyJoins: soft.early_joins_per_day,
        lateFrom: soft.early_days + 1,
        lateHour: soft.late_comments_per_hour,
        lateChannel: soft.late_comments_per_channel_per_day,
        lateJoins: soft.late_joins_per_day,
        firstDay: soft.first_day_comments,
        lastDay: soft.last_day_comments,
      }),
    },
    {
      mode: 'normal',
      name: t('warming.handoff.normalName'),
      detail: t('warming.handoff.normalDetail'),
    },
  ];
  return (
    <Modal onClose={onClose} size="confirm" label={t('warming.handoff.title')}>
      {(close) => (
        <div className="p-6">
          <div className="mb-1 type-h2">{t('warming.handoff.title')}</div>
          <div className="mb-6 type-body text-content-muted">{t('warming.handoff.subtitle')}</div>
          <div
            role="radiogroup"
            aria-label={t('warming.handoff.title')}
            className="mb-6 flex flex-col gap-3"
          >
            {options.map((option) => {
              const selected = option.mode === mode;
              return (
                <button
                  key={option.mode}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  onClick={() => {
                    setMode(option.mode);
                  }}
                  className={`rounded-md border p-4 text-left ${
                    selected ? 'border-action-primary bg-info-tint' : 'border-line bg-surface-card'
                  }`}
                >
                  <div className="type-h3">{option.name}</div>
                  <div className="mt-1 type-small">{option.detail}</div>
                </button>
              );
            })}
          </div>
          <div className="flex justify-end gap-2">
            <Button
              variant="primary"
              onClick={() => {
                onConfirm(mode);
                onClose();
              }}
            >
              {t('warming.handoff.confirm')}
            </Button>
            <Button onClick={close}>{t('warming.handoff.cancel')}</Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
