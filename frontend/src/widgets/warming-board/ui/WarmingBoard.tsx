import { useQuery } from '@tanstack/react-query';
import type { TFunction } from 'i18next';
import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AccountAvatar, accountDisplayName } from '@/entities/account';
import { logsQueryOptions } from '@/entities/log';
import type { LogEntry, WarmingAccountState } from '@/shared/api';
import { eventLabel, eventReason, formatLocalTime, type FeedbackResult } from '@/shared/lib';
import { Card, FeedbackMark, Icon, IconButton } from '@/shared/ui';

import { WarmConfigModal } from './WarmConfigModal';
import { WarmStopModal } from './WarmStopModal';

interface WarmingBoardProps {
  warming: WarmingAccountState[];
  onStop: (accountId: string) => void;
  onPromote: (accountId: string) => void;
  // A set, not one id: stop and promote are per card, the bulk pool button fires
  // one per account, and any two can be in flight at once.
  busyIds: ReadonlySet<string>;
  feedback?: Record<string, FeedbackResult>;
  logLimit?: number;
  // Maps a configured channel handle → its friendly label, so the activity log
  // can show "which channel" a join/read/react touched by name, not raw handle.
  channelLabels?: Record<string, string>;
}

type WarmingState = WarmingAccountState['state'];

// Ordered to match the engine's real cycle: online/subscribe → read → react →
// watch stories → sleep. The old decorative "report" step had no backend action
// and has been dropped; the rail advances by index, so the order must not fight
// the emission order in services/warming (else a completed step would un-fill).
const STAGES = ['subscribe', 'read', 'reactions', 'stories', 'pause'] as const;
const DAY_SEGMENTS = [...Array(42).keys()];
const DAY_TICKS = [0, 4, 7, 11, 14];
const WARMING_DAYS = 14;

// Per-state warming-status pill tone: the token pair the state means, never a
// per-state hex. Sleeping and flood-wait/quarantine share amber deliberately —
// throttled and recovering on its own is not an error.
const WARM_STATUS: Record<WarmingState, string> = {
  active: 'bg-success-tint text-success-deep',
  sleeping: 'bg-warning-tint text-warning-deep',
  idle: 'bg-canvas text-ink-muted',
  flood_wait: 'bg-warning-tint text-warning-deep',
  quarantine: 'bg-warning-tint text-warning-deep',
  error: 'bg-danger-tint text-danger-deep',
};

function extraStr(extra: LogEntry['extra'], key: string): string | undefined {
  const value = extra?.[key];
  return typeof value === 'string' ? value : undefined;
}

// Human-readable tail for a log line: why a reaction was/wasn't placed, how many
// stories were seen — the honest "the engine considered this" breadcrumb — and,
// for every other row, why it turned out the way it did.
function lineDetail(t: TFunction, line: LogEntry): string {
  // The two warming-specific facts win over the general reason because they answer
  // the same question for their own row more precisely, and nothing is lost by
  // preferring them: the gateway reports an outcome it reached (`reaction_skip`,
  // `stories_seen`) while a refusal is a separate `*_failed` row carrying
  // `error_type`, so no row ever holds one of each.
  const skip = extraStr(line.extra, 'reaction_skip');
  if (skip) return t(`logEventReason.${skip}`, { defaultValue: '' });
  const seen = line.extra?.stories_seen;
  // Gateway rows carry the calling domain as a prefix (`warming_telegram_*`).
  if (line.event.endsWith('telegram_watch_peer_stories') && typeof seen === 'number') {
    return seen > 0
      ? t('warming.card.storiesSeen', { count: seen })
      : t('warming.card.storiesNone');
  }
  // Everything else used to end here saying nothing, so a warming failure named the
  // action and never the cause. `reaction_skipped`'s own `extra.reason` is covered by
  // eventReason too — it was the only `reason` warming wrote when this was written,
  // which is why gating on that event name excluded nothing and hid the rest.
  return eventReason(t, line);
}

// The rail reflects the engine's real cycle progress: waiting states park on
// "pause" (+countdown), a running cycle maps its last written action to the
// matching step, idle sits at the start. Keys are the tokens the engine writes
// to last_action (services/warming _PROGRESS_STEPS); values index into STAGES.
const ACTION_STAGE: Record<string, number> = {
  set_online: 0, // subscribe (cycle start)
  join: 0,
  read: 1,
  react: 2, // reactions
  stories: 3,
  // No DM step on the rail; the brief, gated send_dm (runs after stories) folds
  // onto its neighbour rather than adding a step dark for most accounts.
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

// Real per-account activity log, coloured by the log row's status. Tokens, and the
// SAME ones the log terminal uses: these shades are lighter than the light-theme
// success/warning/danger because they sit on the dark surface.
const LOG_TONE: Record<LogEntry['status'], string> = {
  success: 'text-term-success',
  warning: 'text-term-warning',
  error: 'text-term-error',
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
    <span className="ml-auto shrink-0 font-mono text-tiny tabular-nums text-primary-deep">
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
  channelLabels,
}: {
  account: WarmingAccountState;
  onStop: (id: string) => void;
  onPromote: (id: string) => void;
  busy: boolean;
  result?: FeedbackResult;
  logLimit: number;
  channelLabels: Record<string, string>;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [stopOpen, setStopOpen] = useState(false);
  const [cfgOpen, setCfgOpen] = useState(false);
  // One card per account, so the two tooltip ids have to be per-INSTANCE: a literal
  // would collide across the board's rows and `aria-describedby` would resolve every
  // card's badge to the first card's bubble.
  const actionsTipId = useId();
  const cycleTipId = useId();
  // Real per-account activity log, fetched only while the terminal is expanded.
  // `warming_` alone now covers the engine's own gateway calls too: the gateway
  // stamps the calling domain onto its event names, so warming's actions arrive as
  // `warming_telegram_*` while a profile/channel action's bare `telegram_*` row —
  // same account, nothing to do with warming — no longer leaks in. `spam_status_*`
  // is NOT warming-exclusive (several pages probe it) but it is account-scoped and
  // warming drives it on quarantine re-probe, so it stays.
  //
  // Accepted loss, exactly two rows: `telegram_pool_connect_retry` and
  // `telegram_pool_rebuild_hook_failed` (core/telegram_client/_pool.py) no longer reach this
  // card — the "this account's connection is flaky" breadcrumb now lives only on the Logs
  // page. Do NOT bring them back with a `telegram_pool` prefix: the pool is shared, so those
  // rows are exactly as cross-domain as bare `telegram_*` was.
  // Everything else that the old `telegram_` prefix used to surface here still arrives:
  // `telegram_pool_connect_failed` makes the action raise, so the gateway writes
  // `warming_telegram_action_unavailable`; `telegram_spam_status_probe_failed` is folded into
  // `spam_status_refreshed`'s `detail` by `classify_spam_probe`. And
  // `telegram_pool_disconnect_failed` is not a loss at all — it is logged without an
  // `account_id`, so this account-scoped query never returned it in the first place.
  const logQuery = useQuery({
    ...logsQueryOptions({
      query: {
        account_id: account.account_id,
        limit: logLimit,
        event_prefix: 'warming_,spam_status',
      },
    }),
    enabled: open,
  });
  // Client-side "clear": hide everything up to the click; new events still show.
  const [clearedAt, setClearedAt] = useState<number | null>(null);
  const logLines = logQuery.data?.items ?? [];
  const visibleLines =
    clearedAt == null
      ? logLines
      : logLines.filter((line) => new Date(line.created_at).getTime() > clearedAt);
  // Pre-start hold: the account is queued (active) but its first cycle hasn't run
  // yet, so the engine is idle-waiting up to cold_start_spread_hours (plus a snap
  // into the account's morning window) before the first subscribe. Show that
  // honestly with a countdown instead of a blinking "subscribe" step that implies
  // work is happening (services/warming _runner.py).
  const hold =
    account.state === 'active' && (account.cycles_completed ?? 0) === 0 && !account.last_action;
  const active = hold ? -1 : activeStage(account);
  // Real elapsed warming days vs the operator-chosen target (the start slider);
  // the card auto-flips to "complete" once the account reaches its own target.
  const target = account.target_days ?? WARMING_DAYS;
  const elapsed = account.warming_days ?? 0;
  const days = Math.min(elapsed, target);
  const complete = elapsed >= target;
  const filled = Math.round((DAY_SEGMENTS.length * days) / target);
  const dayTicks =
    target === WARMING_DAYS ? DAY_TICKS : [...new Set([0, Math.round(target / 2), target])];
  const connectorPct = hold ? 0 : (active / (STAGES.length - 1)) * 100;
  const statusTone = WARM_STATUS[account.state];
  // Real daily-actions / cap counter (design: "X/N действий"); guard a 0/absent cap.
  const dailyActions = account.daily_actions ?? 0;
  const dailyCap = account.daily_cap && account.daily_cap > 0 ? account.daily_cap : null;
  const actions = dailyCap ? Math.min(dailyActions, dailyCap) : dailyActions;
  const primaryId = accountDisplayName(account);

  return (
    <div className="rounded-lg border border-primary-line bg-primary-tint px-xl py-lg">
      {/* header */}
      <div className="mb-lg flex items-center justify-between">
        <div className="flex min-w-0 items-center gap-md">
          <AccountAvatar
            account={account}
            className="size-icon shrink-0 rounded-full"
            fallbackClassName="text-tiny font-semibold bg-primary-tint text-primary-deep"
          />
          <div className="min-w-0">
            {/* Telegram supplies this name, so it can be one 90-char word with nowhere
                to break: measured, it painted straight out through the card's right
                border. The card's own grid track has a fixed 320px minimum, so the
                name cannot widen the track — it can only spill. `title` keeps the
                whole name reachable now that the card shows a prefix of it. */}
            <div className="truncate type-card-title" title={primaryId}>
              {primaryId}
            </div>
            <div className="mt-hair flex items-center gap-sm">
              {/* Deliberately the dense variant, off the status pill's `3px 10px`/`tiny`
                  rung: this is not a standalone state label but the second line inside a
                  card, paired in one flex row with the `micro` daily-actions counter to
                  its right. On the rung it would tower over the number it is paired
                  with, which is a worse disagreement than differing from the twelve
                  pills on other screens. Twelve on the rung, plus this documented pair. */}
              <span
                className={`inline-flex items-center gap-tight rounded-full px-sm py-px text-tiny font-semibold ${statusTone}`}
              >
                <span className="size-dot rounded-full bg-current" />
                {t(`warming.warmStatus.${account.state}`)}
              </span>
              {/* The app's only two `.tb-tip` triggers that are plain <span>s — the
                  counter here and the "?" below. See `.tb-tip-pop` in
                  app/styles/index.css for what `tabIndex` buys them. */}
              <span className="tb-tip inline-flex items-center">
                <span
                  tabIndex={0}
                  aria-describedby={actionsTipId}
                  className="cursor-help text-tiny font-medium text-ink-subtle"
                >
                  {dailyCap ? `${String(actions)}/${String(dailyCap)}` : String(actions)}
                </span>
                <span id={actionsTipId} role="tooltip" className="tb-tip-pop tb-tip-pop--wide">
                  {t('warming.card.actionsTip')}
                </span>
              </span>
            </div>
          </div>
        </div>
        {/* shrink-0: without it the truncating name above just pushes its cost onto
            the actions instead, and the "Стоп" button loses its label. */}
        <div className="flex shrink-0 items-center gap-sm">
          <span className="tb-tip inline-flex">
            <span
              tabIndex={0}
              aria-describedby={cycleTipId}
              className="inline-flex size-glyph cursor-help items-center justify-center rounded-full border border-primary-line bg-white text-tiny font-bold text-ink-subtle"
            >
              ?
            </span>
            <span id={cycleTipId} role="tooltip" className="tb-tip-pop">
              {t('warming.card.cycleTip', { count: account.cycles_completed ?? 0 })}
              <br />
              <span className={account.dm_allowed ? 'text-term-success' : 'text-term-error'}>
                {t(account.dm_allowed ? 'warming.card.dmAllowed' : 'warming.card.dmClosed')}
              </span>
            </span>
          </span>
          <IconButton
            size="md"
            tone="primary"
            title={t('warming.card.cfgTitle')}
            aria-label={t('warming.card.cfgTitle')}
            onClick={() => {
              setCfgOpen(true);
            }}
          >
            <Icon name="gear" size={14} />
          </IconButton>
          {!complete ? (
            <>
              <FeedbackMark result={result} />
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setStopOpen(true);
                }}
                className="rounded-full border border-line bg-white px-md py-tight text-tiny font-medium text-ink-muted disabled:opacity-50"
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
      {/* The hairline, not a bare fill: this panel is the one place the two pale
          blues used to meet, and with them collapsed it would paint its parent's
          colour and have no edge at all. `primary-hairline` is the rung for exactly
          this — faint enough to double as a divider fill. */}
      <div className="rounded-lg border border-primary-hairline bg-primary-tint px-lg pb-md pt-md">
        <div className="mb-sm flex items-center justify-between">
          <span className="type-caption font-medium">{t('warming.inProgress.days')}</span>
          <span className="type-caption font-bold text-ink">
            {t('warming.card.dayProgress', { days, target, count: target })}
          </span>
        </div>

        {/* day bar */}
        <div className="flex items-end gap-hair">
          {DAY_SEGMENTS.map((index) => (
            <span
              key={index}
              // Days done, the day in progress, days to come — tokens, so the bar
              // reads the same green/blue/grey as the rest of the board.
              className={`h-bar flex-1 rounded-[1.5px] transition-[background] duration-reveal ${index < filled ? 'bg-success' : index === filled ? 'bg-primary' : 'bg-line'}`}
            />
          ))}
        </div>
        <div className="mt-sm flex justify-between px-hair type-caption">
          {dayTicks.map((tick) => (
            <span key={tick}>{tick}</span>
          ))}
        </div>
      </div>

      <div className="px-tight">
        {/* Stepper. Dot and label share ONE cell: as two rows they had different
            geometry — 14px dot cells against full-width label slots, both pinned
            flush by `justify-between` — and the ends drifted 45px apart on a 571px
            card. Equal cells put the first and last dot centres one half-cell
            (50%/STAGES.length) from the edges, which is where the rail has to start
            and stop for the `active / (STAGES.length - 1)` fill to land on a dot. */}
        <div className="relative">
          <div
            className="absolute top-[8px] h-rail overflow-hidden rounded-[2px] bg-primary-line"
            style={{
              left: `${String(50 / STAGES.length)}%`,
              right: `${String(50 / STAGES.length)}%`,
            }}
          >
            <div
              className="absolute left-0 top-0 h-full rounded-[2px] bg-success transition-[width] duration-reveal"
              style={{ width: `${String(connectorPct)}%` }}
            />
          </div>
          <div className="relative flex">
            {STAGES.map((stage, index) => (
              <div key={stage} className="flex flex-1 flex-col items-center">
                <div className="flex size-glyph items-center justify-center">
                  {index < active ? (
                    <span className="tb-pop flex size-spinner items-center justify-center rounded-full bg-success">
                      <Icon name="check" size={10} className="stroke-white" />
                    </span>
                  ) : index === active ? (
                    <span className="tb-livedot size-node rounded-full bg-primary" />
                  ) : (
                    <span className="size-node rounded-full border-[1.5px] border-line-strong bg-white" />
                  )}
                </div>
                <span
                  className={`mt-sm text-center text-tiny ${
                    index < active
                      ? 'font-medium text-success-deep'
                      : index === active
                        ? 'font-semibold text-primary-deep'
                        : 'text-ink-subtle'
                  }`}
                >
                  {t(`warming.stage.${stage}`)}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {!complete ? (
        <>
          {/* current activity */}
          <div className="mt-md flex items-center gap-md rounded-md border border-primary-line bg-primary-tint px-md py-sm">
            <span className="tb-livedot size-dot shrink-0 rounded-full bg-primary" />
            <span className="tb-pulse type-caption font-semibold text-primary-deep">
              {hold ? t('warming.activity.hold') : t(`warming.activity.${STAGES[active]}`)}
            </span>
            {(hold || STAGES[active] === 'pause') && account.next_run_at ? (
              <PauseCountdown nextRunAt={account.next_run_at} />
            ) : null}
          </div>

          {/* activity log */}
          <button
            type="button"
            onClick={() => {
              setOpen((v) => !v);
            }}
            className="mt-md flex w-full items-center justify-center gap-tight border-t border-line-row pt-md text-tiny text-ink-muted"
          >
            {t('warming.card.logToggle')}
            <span
              className={`flex transition-transform duration-reveal ease-spring ${open ? 'rotate-180' : ''}`}
            >
              <Icon name="chevron-down" size={12} />
            </span>
          </button>
          {open ? (
            <div className="mt-md">
              {visibleLines.length > 0 ? (
                <div className="mb-tight flex justify-end">
                  <button
                    type="button"
                    onClick={() => {
                      setClearedAt(Date.now());
                    }}
                    className="inline-flex items-center gap-xs rounded-full border border-line px-sm py-hair text-tiny text-ink-muted transition-colors hover:border-primary-line hover:text-primary-deep"
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
              <div
                // eslint-disable-next-line design-tokens/no-raw-values -- see the note in the rule: this card's own embedded log, one component's internal layout
                className="term tb-scroll max-h-[120px] overflow-y-auto rounded-md bg-term px-md py-md font-mono text-tiny leading-log"
              >
                {visibleLines.length === 0 ? (
                  <div className="text-term-dim">
                    {logQuery.isPending ? t('warming.card.logLoading') : t('warming.card.logEmpty')}
                  </div>
                ) : (
                  visibleLines.map((line) => {
                    // "Which channel" — the gateway logs the touched channel in
                    // extra.channel (or extra.peer for stories); show its label
                    // when configured, else the handle.
                    const rawChannel =
                      extraStr(line.extra, 'channel') ?? extraStr(line.extra, 'peer');
                    const channel = rawChannel
                      ? (channelLabels[rawChannel] ?? rawChannel)
                      : undefined;
                    // "Which reaction" — the emoji the gateway actually placed.
                    const reaction = extraStr(line.extra, 'reaction');
                    const detail = lineDetail(t, line);
                    return (
                      <div key={line.id} className="flex gap-sm">
                        <span className="shrink-0 text-term-dim">
                          {formatLocalTime(line.created_at)}
                        </span>
                        {channel ? (
                          <span className="shrink-0 text-term-link">{channel}</span>
                        ) : null}
                        <span className={LOG_TONE[line.status]}>{eventLabel(t, line.event)}</span>
                        {reaction ? <span className="shrink-0">{reaction}</span> : null}
                        {detail ? <span className="truncate text-term-dim">· {detail}</span> : null}
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          ) : null}
        </>
      ) : (
        <>
          {/* complete */}
          <div className="mt-md flex items-center gap-md rounded-lg border border-success-line bg-success-tint px-md py-md">
            <span className="inline-flex size-chip shrink-0 items-center justify-center rounded-full bg-success">
              <Icon name="check" size={14} className="stroke-white" />
            </span>
            <div className="min-w-0">
              <div className="type-item-title text-success-deep">
                {t('warming.card.completeTitle')}
              </div>
              {/* Grey, while the heading above it is green: the green is already carried by
                  the heading, the tint, the border and the check badge, so this line is
                  supporting prose and not a third copy of the same signal. It is also the
                  only way it reaches AA — every green dark enough to pass on `success-tint`
                  is indistinguishable from the heading's `success-deep` (10.05:1 here
                  against 3.70:1 for the old literal). Do not "restore the family". */}
              <div className="mt-px type-caption text-ink-body">
                {t('warming.card.completeSub', {
                  days: t('warming.card.dayProgress', { days, target, count: target }),
                })}
              </div>
            </div>
          </div>
          <div className="mt-md flex items-center gap-sm">
            <FeedbackMark result={result} />
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                onPromote(account.account_id);
              }}
              className="flex flex-1 items-center justify-center gap-sm rounded-full bg-success-deep px-lg py-md text-body font-semibold text-white transition-colors hover:bg-success-press disabled:opacity-50"
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
  busyIds,
  feedback = {},
  logLimit = DEFAULT_CARD_LOG_LIMIT,
  channelLabels = {},
}: WarmingBoardProps) {
  const { t } = useTranslation();
  return (
    <Card className="p-lg">
      <div className="mb-lg flex items-center justify-between">
        <div className="flex items-center gap-md">
          <span className="flex size-icon items-center justify-center rounded-md bg-primary">
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              className="stroke-white"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M3 12h4l3 8 4-16 3 8h4" />
            </svg>
          </span>
          <span className="type-card-title">{t('warming.inProgress.title')}</span>
        </div>
        {warming.length > 0 ? (
          <span className="tb-pulse rounded-full bg-success-tint px-md py-xs text-tiny font-semibold text-success-deep">
            {t('warming.inProgress.live')}
          </span>
        ) : null}
      </div>

      <div className="grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] items-start gap-md">
        {warming.map((account) => (
          <WarmingCard
            key={account.account_id}
            account={account}
            onStop={onStop}
            onPromote={onPromote}
            busy={busyIds.has(account.account_id)}
            result={feedback[account.account_id]}
            logLimit={logLimit}
            channelLabels={channelLabels}
          />
        ))}
        {warming.length === 0 ? (
          <div className="col-span-full rounded-lg border-[1.5px] border-dashed border-primary-line px-md py-[50px] text-center text-body text-ink-subtle">
            {t('warming.column.empty')}
          </div>
        ) : null}
      </div>
    </Card>
  );
}
