import { useTranslation } from 'react-i18next';

import type { ChallengeRow } from '@/shared/api';
import { Card, Switch } from '@/shared/ui';

import { CaptchaQueue } from './CaptchaQueue';

// The captcha-solver card: the per-campaign solver toggle plus the pending
// bot-challenge queue (shown only while the solver is on and the queue is
// non-empty).
export function CaptchaSolverCard({
  solverEnabled,
  campaignId,
  onToggleSolver,
  captchaQueue,
  accountLabel,
}: {
  solverEnabled: boolean;
  campaignId: string | null;
  onToggleSolver: () => void;
  captchaQueue: ChallengeRow[];
  accountLabel: (accountId: string) => string;
}) {
  const { t } = useTranslation();
  return (
    <Card className="">
      <div className="flex items-center justify-between gap-md px-lg py-md">
        <div className="flex min-w-0 items-center gap-md">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary-tint text-primary-deep">
            <svg
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M9 12l2 2 4-4" />
              <path d="M12 3a9 9 0 1 0 9 9 9 9 0 0 0-9-9z" />
            </svg>
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-sm">
              <span className="text-body font-semibold text-ink">
                {t('neurocomment.captcha.title')}
              </span>
              <span className="tb-tip inline-flex">
                <span className="inline-flex h-[15px] w-[15px] cursor-help items-center justify-center rounded-full border border-line bg-white text-micro font-bold text-ink-subtle">
                  ?
                </span>
                <span className="tb-tip-pop tb-tip-pop--wide" style={{ textAlign: 'left' }}>
                  {t('neurocomment.captcha.tooltip')}
                </span>
              </span>
            </div>
            <div className="text-micro leading-[1.35] text-ink-subtle">
              {t('neurocomment.captcha.sub')}
            </div>
          </div>
        </div>
        <Switch
          checked={solverEnabled}
          onChange={onToggleSolver}
          label={t('neurocomment.captcha.title')}
          disabled={campaignId === null}
        />
      </div>
      {solverEnabled && captchaQueue.length > 0 ? (
        <div className="px-lg pb-lg">
          <div className="mb-md flex items-center gap-sm border-t border-line-row pt-md">
            <svg
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="stroke-warning"
            >
              <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />
              <path d="M12 8v4" />
              <path d="M12 16h.01" />
            </svg>
            <span className="text-tiny font-semibold uppercase tracking-[.03em] text-warning-deep">
              {t('neurocomment.captcha.pending', { count: captchaQueue.length })}
            </span>
          </div>
          <CaptchaQueue rows={captchaQueue} accountLabel={accountLabel} />
        </div>
      ) : null}
    </Card>
  );
}
