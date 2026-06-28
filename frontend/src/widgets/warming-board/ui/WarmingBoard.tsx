import { useTranslation } from 'react-i18next';

import { WarmingStateBadge } from '@/entities/warming';
import type { WarmingAccountState } from '@/shared/api';

interface WarmingBoardProps {
  warming: WarmingAccountState[];
  onStop: (accountId: string) => void;
  busyId: string | null;
}

const STAGES = ['subscribe', 'read', 'stories', 'reactions', 'pause', 'report'] as const;

function mono(id: string): string {
  return id.replace(/\D/g, '').slice(-2) || id.slice(0, 2).toUpperCase();
}

// ponytail: no per-account phase field on the board read model yet, so derive a
// display stage from cycles/state. Decorative until the API exposes current_phase.
function activeStage(account: WarmingAccountState): number {
  if (account.state === 'sleeping') return 4;
  if (account.state === 'idle') return 0;
  return (account.cycles_completed ?? 0) % STAGES.length;
}

// The design's "Warming" panel: blue-tinted in-progress cards, each with a
// six-stage pipeline stepper (done ✓ / active ● / pending ○).
export function WarmingBoard({ warming, onStop, busyId }: WarmingBoardProps) {
  const { t } = useTranslation();
  return (
    <div className="rounded-2xl border border-line bg-white p-4">
      <div className="mb-[14px] flex items-center justify-between">
        <div className="flex items-center gap-[9px]">
          <span className="flex h-[30px] w-[30px] items-center justify-center rounded-[9px] bg-primary">
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="#fff"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M3 12h4l3 8 4-16 3 8h4" />
            </svg>
          </span>
          <span className="text-[14px] font-bold">{t('warming.inProgress.title')}</span>
        </div>
        {warming.length > 0 ? (
          <span className="tb-pulse rounded-full bg-success-tint px-[10px] py-[3px] text-[11px] font-semibold text-success">
            {t('warming.inProgress.live')}
          </span>
        ) : null}
      </div>

      <div className="grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] items-start gap-3">
        {warming.map((account) => {
          const active = activeStage(account);
          return (
            <div
              key={account.account_id}
              className="rounded-[14px] border border-[#e4ecfa] bg-[#f7faff] p-[16px_17px]"
            >
              <div className="mb-4 flex items-center justify-between">
                <div className="flex items-center gap-[9px]">
                  <div className="flex h-7 w-7 items-center justify-center rounded-full bg-primary-tint text-[11px] font-semibold text-primary">
                    {mono(account.account_id)}
                  </div>
                  <div>
                    <div className="text-[13px] font-semibold">{account.account_id}</div>
                    <div className="mt-[2px]">
                      <WarmingStateBadge state={account.state} />
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  disabled={busyId === account.account_id}
                  onClick={() => {
                    onStop(account.account_id);
                  }}
                  className="rounded-full border border-line bg-white px-[11px] py-[5px] text-[11px] font-medium text-ink-muted disabled:opacity-50"
                >
                  {t('warming.actions.stop')}
                </button>
              </div>

              <div className="rounded-[11px] bg-white/60 px-[13px] pb-[9px] pt-[11px]">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-[10px] font-medium text-ink-muted">
                    {t('warming.inProgress.days')}
                  </span>
                  <span className="text-[10px] font-bold text-ink">
                    {t('warming.card.cycles', { count: account.cycles_completed })}
                  </span>
                </div>

                {/* stepper */}
                <div className="relative flex items-center justify-between px-[6px]">
                  <div className="absolute inset-x-[7px] top-1/2 h-[2px] -translate-y-1/2 rounded bg-[#dce2ec]" />
                  {STAGES.map((stage, index) => (
                    <div
                      key={stage}
                      className="relative z-10 flex h-[14px] w-[14px] items-center justify-center"
                    >
                      {index < active ? (
                        <span className="flex h-[14px] w-[14px] items-center justify-center rounded-full bg-success">
                          <svg
                            width="9"
                            height="9"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="#fff"
                            strokeWidth="3.4"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          >
                            <path d="M20 6 9 17l-5-5" />
                          </svg>
                        </span>
                      ) : index === active ? (
                        <span className="tb-livedot h-[10px] w-[10px] rounded-full bg-primary" />
                      ) : (
                        <span className="h-[9px] w-[9px] rounded-full border-[1.5px] border-[#d2d0cc] bg-white" />
                      )}
                    </div>
                  ))}
                </div>
                <div className="mt-2 flex justify-between">
                  {STAGES.map((stage, index) => (
                    <span
                      key={stage}
                      className={`flex-1 text-center text-[9px] ${index === active ? 'font-semibold text-primary' : 'text-ink-subtle'}`}
                    >
                      {t(`warming.stage.${stage}`)}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
