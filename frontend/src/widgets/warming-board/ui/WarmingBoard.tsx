import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { logsQueryOptions } from '@/entities/log';
import type { LogEntry, WarmingAccountState } from '@/shared/api';
import { eventLabel, formatLocalTime, type FeedbackResult } from '@/shared/lib';
import { FeedbackMark } from '@/shared/ui';

import { WarmConfigModal } from './WarmConfigModal';
import { WarmStopModal } from './WarmStopModal';

interface WarmingBoardProps {
  warming: WarmingAccountState[];
  onStop: (accountId: string) => void;
  onPromote: (accountId: string) => void;
  busyId: string | null;
  feedback?: Record<string, FeedbackResult>;
  logLimit?: number;
}

type WarmingState = WarmingAccountState['state'];

const STAGES = ['subscribe', 'read', 'stories', 'reactions', 'pause', 'report'] as const;
const DAY_SEGMENTS = [...Array(42).keys()];
const DAY_TICKS = [0, 4, 7, 11, 14];
const WARMING_DAYS = 14;

// The design's per-state warming-status pill colours (warmStatusColor/Bg).
const WARM_STATUS: Record<WarmingState, { color: string; bg: string }> = {
  active: { color: '#12a150', bg: '#ddf7e9' },
  sleeping: { color: '#c47d12', bg: '#fbf3e2' },
  idle: { color: '#74726e', bg: '#eeedea' },
  flood_wait: { color: '#9a7b22', bg: '#fbf3e2' },
  quarantine: { color: '#9a7b22', bg: '#fbf3e2' },
  error: { color: '#c0473f', bg: '#fbecec' },
};

function mono(id: string): string {
  return id.replace(/\D/g, '').slice(-2) || id.slice(0, 2).toUpperCase();
}

// The rail reflects the engine's real cycle progress: waiting states park on
// "pause" (+countdown), a running cycle maps its last written action to the
// matching step, idle sits at the start. The last "report" step has no backend
// action, so it is never lit — the end of a cycle is the pause, not a report.
const ACTION_STAGE: Record<string, number> = {
  set_online: 0, // subscribe
  join: 0,
  read: 1, // read
  react: 3, // reactions
  // The fixed 6-label rail has no DM step; the brief, gated send_dm folds onto
  // the nearest prior activity (reactions) rather than adding a 7th label.
  send_dm: 3,
};

function activeStage(account: WarmingAccountState): number {
  const { state } = account;
  if (state === 'sleeping' || state === 'flood_wait' || state === 'quarantine') return 4; // pause
  if (state === 'idle') return 0;
  // active / error both show where the engine last was (core/warming _loop.py);
  // an error is distinguished by the red header pill, not a separate rail step.
  return ACTION_STAGE[account.last_action ?? ''] ?? 0;
}

// Real per-account activity log, coloured by the log row's status.
const LOG_COLOR: Record<LogEntry['status'], string> = {
  success: '#7FCDA0',
  warning: '#E0B341',
  error: '#E5736B',
};
// Fallback only — the board serves the real limit from config (card_log_limit).
const DEFAULT_CARD_LOG_LIMIT = 20;

// Live countdown to the next cycle (``next_run_at``), shown beside the pause
// activity so the operator sees how long the "natural" pause lasts. Renders
// nothing once the target passes or when there is no scheduled next run.
function PauseCountdown({ nextRunAt }: { nextRunAt: string }) {
  const { t } = useTranslation();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => {
      clearInterval(id);
    };
  }, []);
  const target = new Date(nextRunAt).getTime();
  if (Number.isNaN(target)) return null;
  const totalSec = Math.round((target - now) / 1000);
  if (totalSec <= 0) return null;
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  const time = h > 0 ? `${String(h)}:${pad(m)}:${pad(s)}` : `${String(m)}:${pad(s)}`;
  return (
    <span className="ml-auto shrink-0 font-mono text-[11px] tabular-nums text-primary/70">
      {t('warming.card.pauseCountdown', { time })}
    </span>
  );
}

function WarmingCard({
  account,
  onStop,
  onPromote,
  busy,
  result,
  logLimit,
}: {
  account: WarmingAccountState;
  onStop: (id: string) => void;
  onPromote: (id: string) => void;
  busy: boolean;
  result?: FeedbackResult;
  logLimit: number;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [stopOpen, setStopOpen] = useState(false);
  const [cfgOpen, setCfgOpen] = useState(false);
  // Real per-account activity log, fetched only while the terminal is expanded.
  const logQuery = useQuery({
    ...logsQueryOptions({ query: { account_id: account.account_id, limit: logLimit } }),
    enabled: open,
  });
  // Client-side "clear": hide everything up to the click; new events still show.
  const [clearedAt, setClearedAt] = useState<number | null>(null);
  const logLines = logQuery.data?.items ?? [];
  const visibleLines =
    clearedAt == null
      ? logLines
      : logLines.filter((line) => new Date(line.created_at).getTime() > clearedAt);
  const active = activeStage(account);
  // Real elapsed warming days vs the operator-chosen target (the start slider);
  // the card auto-flips to "complete" once the account reaches its own target.
  const target = account.target_days ?? WARMING_DAYS;
  const elapsed = account.warming_days ?? 0;
  const days = Math.min(elapsed, target);
  const complete = elapsed >= target;
  const filled = Math.round((DAY_SEGMENTS.length * days) / target);
  const dayTicks =
    target === WARMING_DAYS ? DAY_TICKS : [...new Set([0, Math.round(target / 2), target])];
  const connectorPct = (active / (STAGES.length - 1)) * 100;
  const status = WARM_STATUS[account.state];
  // Real daily-actions / cap counter (design: "X/N действий"); guard a 0/absent cap.
  const dailyActions = account.daily_actions ?? 0;
  const dailyCap = account.daily_cap && account.daily_cap > 0 ? account.daily_cap : null;
  const actions = dailyCap ? Math.min(dailyActions, dailyCap) : dailyActions;
  const primaryId = account.phone ?? account.label ?? account.account_id;

  return (
    <div className="rounded-[14px] border border-[#e4ecfa] bg-[#f7faff] px-[17px] py-4">
      {/* header */}
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-[9px]">
          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-[#e8f0ff] text-[11px] font-semibold text-[#0066ff]">
            {mono(primaryId)}
          </div>
          <div>
            <div className="text-[13px] font-semibold">{primaryId}</div>
            <div className="mt-[2px] flex items-center gap-[6px]">
              <span
                className="inline-flex items-center gap-1 rounded-full px-[7px] py-px text-[10.5px] font-semibold"
                style={{ color: status.color, background: status.bg }}
              >
                <span
                  className="h-[5px] w-[5px] rounded-full"
                  style={{ background: status.color }}
                />
                {t(`warming.warmStatus.${account.state}`)}
              </span>
              <span className="tb-tip inline-flex items-center">
                <span className="cursor-help text-[10.5px] font-medium text-ink-subtle">
                  {dailyCap ? `${String(actions)}/${String(dailyCap)}` : String(actions)}
                </span>
                <span className="tb-tip-pop tb-tip-pop--wide">{t('warming.card.actionsTip')}</span>
              </span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-[7px]">
          <span className="tb-tip inline-flex">
            <span className="inline-flex h-[18px] w-[18px] cursor-help items-center justify-center rounded-full border border-[#cbd7ec] bg-white text-[11px] font-bold text-[#7a8aa6]">
              ?
            </span>
            <span className="tb-tip-pop">
              {t('warming.card.cycleTip', { count: account.cycles_completed ?? 0 })}
              <br />
              <span style={{ color: account.dm_allowed ? '#5FD08A' : '#F08C84' }}>
                {t(account.dm_allowed ? 'warming.card.dmAllowed' : 'warming.card.dmClosed')}
              </span>
            </span>
          </span>
          <button
            type="button"
            title={t('warming.card.cfgTitle')}
            onClick={() => {
              setCfgOpen(true);
            }}
            className="inline-flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full border border-line bg-white text-ink-muted transition-colors hover:border-[#cbd7ec] hover:bg-[#f2f6ff] hover:text-primary"
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>
          {!complete ? (
            <>
              <FeedbackMark result={result} />
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setStopOpen(true);
                }}
                className="rounded-full border border-line bg-white px-[11px] py-[5px] text-[11px] font-medium text-ink-muted disabled:opacity-50"
              >
                {t('warming.actions.stopShort')}
              </button>
            </>
          ) : null}
        </div>
      </div>

      {stopOpen ? (
        <WarmStopModal
          phone={primaryId}
          onClose={() => {
            setStopOpen(false);
          }}
          onStop={() => {
            onStop(account.account_id);
          }}
          onFinish={() => {
            onPromote(account.account_id);
          }}
        />
      ) : null}
      {cfgOpen ? (
        <WarmConfigModal
          phone={primaryId}
          onClose={() => {
            setCfgOpen(false);
          }}
        />
      ) : null}

      {/* pipeline */}
      <div className="rounded-[11px] bg-[#f7faff] px-[13px] pb-[9px] pt-[11px]">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[10px] font-medium text-ink-muted">
            {t('warming.inProgress.days')}
          </span>
          <span className="text-[10px] font-bold text-ink">
            {t('warming.card.dayProgress', { days, target, count: target })}
          </span>
        </div>

        {/* day bar */}
        <div className="flex items-end gap-[2px]">
          {DAY_SEGMENTS.map((index) => (
            <span
              key={index}
              className="h-[22px] flex-1 rounded-[1.5px] transition-[background] duration-[400ms]"
              style={{
                background: index < filled ? '#12a150' : index === filled ? '#0066ff' : '#e4e2de',
              }}
            />
          ))}
        </div>
        <div className="mt-[7px] flex justify-between px-[2px] text-[9.5px] text-[#7a7a7e]">
          {dayTicks.map((tick) => (
            <span key={tick}>{tick}</span>
          ))}
        </div>
      </div>

      <div className="px-[6px]">
        {/* stepper */}
        <div className="relative h-[16px]">
          <div className="absolute inset-x-[7px] top-1/2 h-[2px] -translate-y-1/2 overflow-hidden rounded-[2px] bg-[#dce2ec]">
            <div
              className="absolute left-0 top-0 h-full rounded-[2px] bg-success transition-[width] duration-500"
              style={{ width: `${String(connectorPct)}%` }}
            />
          </div>
          <div className="relative flex h-[16px] items-center justify-between">
            {STAGES.map((stage, index) => (
              <div
                key={stage}
                className="relative flex h-[14px] w-[14px] shrink-0 items-center justify-center"
              >
                {index < active ? (
                  <span className="tb-pop flex h-[14px] w-[14px] items-center justify-center rounded-full bg-success">
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
        </div>
        <div className="mt-2 flex justify-between">
          {STAGES.map((stage, index) => (
            <span
              key={stage}
              className={`flex-1 text-center text-[9px] ${
                index < active
                  ? 'font-medium text-success'
                  : index === active
                    ? 'font-semibold text-primary'
                    : 'text-ink-subtle'
              }`}
            >
              {t(`warming.stage.${stage}`)}
            </span>
          ))}
        </div>
      </div>

      {!complete ? (
        <>
          {/* current activity */}
          <div className="mt-[11px] flex items-center gap-[9px] rounded-[9px] border border-[#dce7fb] bg-[#eef4ff] px-[10px] py-[7px]">
            <span className="tb-livedot h-2 w-2 shrink-0 rounded-full bg-primary" />
            <span className="tb-pulse text-[11.5px] font-semibold text-primary">
              {t(`warming.activity.${STAGES[active]}`)}
            </span>
            {STAGES[active] === 'pause' && account.next_run_at ? (
              <PauseCountdown nextRunAt={account.next_run_at} />
            ) : null}
          </div>

          {/* activity log */}
          <button
            type="button"
            onClick={() => {
              setOpen((v) => !v);
            }}
            className="mt-[11px] flex w-full items-center justify-center gap-[5px] border-t border-[#f0eeeb] pt-[9px] text-[11px] text-ink-muted"
          >
            {t('warming.card.logToggle')}
            <span
              className={`flex transition-transform duration-[420ms] [transition-timing-function:cubic-bezier(.34,1.45,.6,1)] ${open ? 'rotate-180' : ''}`}
            >
              <svg
                width="12"
                height="12"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="m6 9 6 6 6-6" />
              </svg>
            </span>
          </button>
          {open ? (
            <div className="mt-[9px]">
              {visibleLines.length > 0 ? (
                <div className="mb-[5px] flex justify-end">
                  <button
                    type="button"
                    onClick={() => {
                      setClearedAt(Date.now());
                    }}
                    className="inline-flex items-center gap-[4px] rounded-full border border-line px-[8px] py-[2px] text-[10px] text-ink-muted transition-colors hover:border-[#cbd7ec] hover:text-primary"
                  >
                    <svg
                      width="10"
                      height="10"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />
                    </svg>
                    {t('warming.card.logClear')}
                  </button>
                </div>
              ) : null}
              <div className="term tb-scroll max-h-[120px] overflow-y-auto rounded-[9px] bg-[#16161a] px-[11px] py-[10px] font-mono text-[10.5px] leading-[1.7]">
                {visibleLines.length === 0 ? (
                  <div className="text-[#5c5c66]">
                    {logQuery.isPending ? t('warming.card.logLoading') : t('warming.card.logEmpty')}
                  </div>
                ) : (
                  visibleLines.map((line) => (
                    <div key={line.id} className="flex gap-2">
                      <span className="shrink-0 text-[#5c5c66]">
                        {formatLocalTime(line.created_at)}
                      </span>
                      <span style={{ color: LOG_COLOR[line.status] }}>
                        {eventLabel(t, line.event)}
                      </span>
                    </div>
                  ))
                )}
              </div>
            </div>
          ) : null}
        </>
      ) : (
        <>
          {/* complete */}
          <div className="mt-[11px] flex items-center gap-[10px] rounded-[10px] border border-[#b8ecce] bg-[#ddf7e9] px-[12px] py-[10px]">
            <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-success">
              <svg
                width="13"
                height="13"
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
            <div className="min-w-0">
              <div className="text-[12.5px] font-bold text-[#0b6b37]">
                {t('warming.card.completeTitle')}
              </div>
              <div className="mt-px text-[10.5px] text-[#3f8a5e]">
                {t('warming.card.completeSub', {
                  days: t('warming.card.dayProgress', { days, target, count: target }),
                })}
              </div>
            </div>
          </div>
          <div className="mt-[9px] flex items-center gap-[8px]">
            <FeedbackMark result={result} />
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                onPromote(account.account_id);
              }}
              className="flex flex-1 items-center justify-center gap-[7px] rounded-full bg-success px-[14px] py-[10px] text-[12px] font-semibold text-white transition-colors hover:bg-[#0e8c45] disabled:opacity-50"
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M5 12h14" />
                <path d="m12 5 7 7-7 7" />
              </svg>
              {t('warming.card.finish')}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// The design's "Warming" panel: blue-tinted in-progress cards, each with the
// day-bar histogram, six-stage pipeline stepper, live current-activity row,
// expandable terminal log, and completion state.
export function WarmingBoard({
  warming,
  onStop,
  onPromote,
  busyId,
  feedback = {},
  logLimit = DEFAULT_CARD_LOG_LIMIT,
}: WarmingBoardProps) {
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
        {warming.map((account) => (
          <WarmingCard
            key={account.account_id}
            account={account}
            onStop={onStop}
            onPromote={onPromote}
            busy={busyId === account.account_id}
            result={feedback[account.account_id]}
            logLimit={logLimit}
          />
        ))}
        {warming.length === 0 ? (
          <div className="col-span-full rounded-xl border-[1.5px] border-dashed border-[#dce7fb] px-[10px] py-[50px] text-center text-[13px] text-ink-subtle">
            {t('warming.column.empty')}
          </div>
        ) : null}
      </div>
    </div>
  );
}
