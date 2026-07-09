import { useTranslation } from 'react-i18next';

import type { ChallengeRow } from '@/shared/api';

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
  onSolve,
}: {
  solverEnabled: boolean;
  campaignId: string | null;
  onToggleSolver: () => void;
  captchaQueue: ChallengeRow[];
  accountLabel: (accountId: string) => string;
  onSolve: (item: ChallengeRow) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="rounded-2xl border border-line bg-white">
      <div className="flex items-center justify-between gap-[10px] px-[14px] py-3">
        <div className="flex min-w-0 items-center gap-[9px]">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary-tint text-primary">
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
            <div className="flex items-center gap-[6px]">
              <span className="text-[12.5px] font-semibold text-ink">
                {t('neurocomment.captcha.title')}
              </span>
              <span className="tb-tip inline-flex">
                <span className="inline-flex h-[15px] w-[15px] cursor-help items-center justify-center rounded-full border border-line-input bg-white text-[10px] font-bold text-ink-subtle">
                  ?
                </span>
                <span className="tb-tip-pop tb-tip-pop--wide">
                  {t('neurocomment.captcha.tooltip')}
                </span>
              </span>
            </div>
            <div className="text-[10.5px] leading-[1.35] text-ink-subtle">
              {t('neurocomment.captcha.sub')}
            </div>
          </div>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={solverEnabled}
          aria-label={t('neurocomment.captcha.title')}
          disabled={campaignId === null}
          onClick={onToggleSolver}
          className="tb-sw relative h-[26px] w-[46px] shrink-0 rounded-full transition-colors disabled:opacity-50"
          style={{ background: solverEnabled ? '#0066ff' : '#d8d6d2' }}
        >
          <span
            className="absolute top-[3px] transition-[left] duration-200"
            style={{ left: solverEnabled ? '23px' : '3px' }}
          >
            <span className="tb-sw-thumb block h-5 w-5 rounded-full bg-white shadow-[0_1px_3px_rgba(0,0,0,0.3)]" />
          </span>
        </button>
      </div>
      {solverEnabled && captchaQueue.length > 0 ? (
        <div className="px-[14px] pb-[14px]">
          <div className="mb-[9px] flex items-center gap-[7px] border-t border-[#f0eeea] pt-[11px]">
            <svg
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="#9a7b22"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />
              <path d="M12 8v4" />
              <path d="M12 16h.01" />
            </svg>
            <span className="text-[11px] font-semibold uppercase tracking-[.03em] text-[#9a7b22]">
              {t('neurocomment.captcha.pending', { count: captchaQueue.length })}
            </span>
          </div>
          <CaptchaQueue rows={captchaQueue} accountLabel={accountLabel} onSolve={onSolve} />
        </div>
      ) : null}
    </div>
  );
}
