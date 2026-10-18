import { useQuery } from '@tanstack/react-query';
import type { TFunction } from 'i18next';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AccountAvatar, accountDisplayName } from '@/entities/account';
import { logsQueryOptions } from '@/entities/log';
import { SpamBlockBadge, SurvivalBadge, WaveChip } from '@/entities/warming';
import { PhoneScreen } from '@/features/phone-screen';
import type { LogEntry, WarmingAccountState } from '@/shared/api';
import { badgeTone, type BadgeTone } from '@/shared/design-system';
import { eventLabel, eventReason, formatLocalTime, type FeedbackResult } from '@/shared/lib';
import {
  Badge,
  Button,
  Card,
  CardHeader,
  FeedbackMark,
  Icon,
  IconButton,
  Modal,
  ModalHeader,
  ProgressBar,
  TerminalPane,
} from '@/shared/ui';

import { FloodMemoryLine } from './FloodMemoryLine';
import { SpamBlockPanel } from './SpamBlockPanel';
import { AttentionRow, PhaseRow, TodayRow, VisitRow } from './WarmingCardRows';
import { WarmDayCount } from './WarmDayCount';
import { WarmStopModal } from './WarmStopModal';

interface WarmingBoardProps {
  warming: WarmingAccountState[];
  onStop: (accountId: string) => void;
  onPromote: (accountId: string) => void;
  // A confirmed spam block's two answers: the account delete dialog, or keep it.
  onDelete?: (accountId: string) => void;
  onKeep?: (accountId: string) => void;
  // A set, not one id: stop and promote are per card, the bulk pool button fires
  // one per account, and any two can be in flight at once.
  busyIds: ReadonlySet<string>;
  feedback?: Record<string, FeedbackResult>;
  logLimit?: number;
  // Maps a configured channel handle → its friendly label, so the activity log
  // can show "which channel" a join/read/react touched by name, not raw handle.
  channelLabels?: Record<string, string>;
  // The target a card falls back to when its stint carries none — the board's
  // `default_target_days`, the same length a start without a pick runs for.
  defaultTargetDays?: number;
}

type WarmingState = WarmingAccountState['state'];

const DAY_SEGMENTS = 42;

function noop(): void {
  // A board without the spam-block actions wired (they show only on such a card).
}
// The target a card falls back to when the board has not answered yet.
const TWO_WEEK_DAYS = 14;

// Per-state warming-status pill tone. Sleeping and flood-wait/quarantine share amber
// deliberately — throttled and recovering on its own is not an error.
//
// Тон назван ТОНОМ, а не парой классов, которую тон означает. Пары стояли здесь и
// совпадали с `badgeTone()` во всех шести строках дословно, включая порядок — то есть это
// был шестой экземпляр той же таблицы, и разошёлся бы он молча: плашка на других экранах
// поехала бы за рецептом, а эта осталась бы прежней. Тип `BadgeTone` — общий набор из
// `recipes/feedback.ts`; `neutral` тут носит `idle`, у которого смысла и нет.
const WARM_STATUS: Record<WarmingState, BadgeTone> = {
  active: 'success',
  sleeping: 'warning',
  idle: 'neutral',
  flood_wait: 'warning',
  quarantine: 'warning',
  error: 'danger',
  spam_blocked: 'danger',
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
  // How much of the video/voice the account actually pulled — the gateway's bounded
  // partial download, which is also what the proxy paid for.
  const bytes = line.extra?.bytes;
  if (line.event.endsWith('telegram_warm_consume_media') && typeof bytes === 'number') {
    return t('warming.card.mediaBytes', { kb: Math.round(bytes / 1024) });
  }
  // Everything else used to end here saying nothing, so a warming failure named the
  // action and never the cause. `reaction_skipped`'s own `extra.reason` is covered by
  // eventReason too — it was the only `reason` warming wrote when this was written,
  // which is why gating on that event name excluded nothing and hid the rest. The
  // extras' `warm_skip` ("ran, nothing to do") goes through the same ladder.
  return eventReason(t, line);
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

function WarmingCard({
  account,
  onStop,
  onPromote,
  onDelete,
  onKeep,
  busy,
  result,
  logLimit,
  channelLabels,
  defaultTargetDays,
}: {
  account: WarmingAccountState;
  onStop: (id: string) => void;
  onPromote: (id: string) => void;
  onDelete: (id: string) => void;
  onKeep: (id: string) => void;
  busy: boolean;
  result?: FeedbackResult;
  logLimit: number;
  channelLabels: Record<string, string>;
  defaultTargetDays: number;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [stopOpen, setStopOpen] = useState(false);
  // The phone being watched, fixed when the eye was clicked: the visit ending clears
  // `live_phone_id`, and the open screen then says so instead of vanishing — and a
  // later visit never reopens it on its own.
  const [watching, setWatching] = useState<string | null>(null);
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
  // Worked warming days vs the operator-chosen target (the start slider); days in
  // error or quarantine do not count. The card auto-flips to "complete" once the
  // account reaches its own target — the same count the engine's gate reads.
  const target = account.target_days ?? defaultTargetDays;
  const elapsed = account.warmed_days ?? 0;
  const days = Math.min(elapsed, target);
  // Warming stopped on a confirmed spam block: the card waits for the operator's call.
  const blocked = account.state === 'spam_blocked';
  const complete = elapsed >= target && !blocked;
  const statusTone = badgeTone(WARM_STATUS[account.state]);
  const primaryId = accountDisplayName(account);

  return (
    <div className="rounded-md border border-info-line bg-info-tint px-6 py-4">
      {/* header */}
      <div className="mb-4 flex items-center justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <AccountAvatar
            account={account}
            className="size-icon shrink-0 rounded-full"
            fallbackClassName="text-small font-medium bg-info-tint text-info-strong"
          />
          <div className="min-w-0">
            {/* Telegram supplies this name, so it can be one 90-char word with nowhere
                to break: measured, it painted straight out through the card's right
                border. The card's own grid track has a fixed 320px minimum, so the
                name cannot widen the track — it can only spill. `title` keeps the
                whole name reachable now that the card shows a prefix of it. */}
            <div className="truncate type-h3" title={primaryId}>
              {primaryId}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              {/* Deliberately the dense variant, off the status pill's `3px 10px`/`tiny`
                  rung: this is the second line inside a card, not a standalone label. */}
              <span
                className={`inline-flex items-center gap-1 rounded-full px-2 text-small font-medium ${statusTone}`}
              >
                <span className="size-dot rounded-full bg-current" />
                {t(`warming.warmStatus.${account.state}`)}
              </span>
              {account.geo_accepted ? <Badge>{t('warming.geo.accepted')}</Badge> : null}
              <SpamBlockBadge blockedAt={account.spam_blocked_at} />
              <WaveChip
                accountId={account.account_id}
                name={primaryId}
                wave={account.wave}
                startedAt={account.started_at}
              />
              {/* A resumed account was released before: its life after release goes on. */}
              {account.survival ? <SurvivalBadge survival={account.survival} /> : null}
            </div>
          </div>
        </div>
        {/* shrink-0: without it the truncating name above just pushes its cost onto
            the actions instead, and the "Стоп" button loses its label. */}
        <div className="flex shrink-0 items-center gap-2">
          {/* Only while a mobile visit runs: between visits the phone sleeps and
              there is no screen to show. Watching never takes the controls. */}
          {account.live_phone_id ? (
            <IconButton
              size="sm"
              aria-label={t('warming.card.watchPhone')}
              title={t('warming.card.watchPhone')}
              onClick={() => {
                setWatching(account.live_phone_id ?? null);
              }}
            >
              <Icon name="eye" size={14} />
            </IconButton>
          ) : null}
          {!complete && !blocked ? (
            <>
              <FeedbackMark result={result} />
              <Button
                size="sm"
                disabled={busy}
                onClick={() => {
                  setStopOpen(true);
                }}
                className="text-content-muted"
              >
                {t('warming.actions.stopShort')}
              </Button>
            </>
          ) : null}
        </div>
      </div>

      <PhaseRow account={account} />
      <FloodMemoryLine account={account} />

      {watching ? (
        <Modal
          size="panel"
          label={t('warming.card.watchPhoneTitle')}
          onClose={() => {
            setWatching(null);
          }}
        >
          <ModalHeader title={t('warming.card.watchPhoneTitle')} subtitle={primaryId} />
          <div className="p-6">
            <PhoneScreen phoneId={watching} mode="view" />
          </div>
        </Modal>
      ) : null}
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
      {/* pipeline */}
      {/* The hairline, not a bare fill: this panel is the one place the two pale
          blues used to meet, and with them collapsed it would paint its parent's
          colour and have no edge at all. `primary-hairline` is the rung for exactly
          this — faint enough to double as a divider fill. */}
      <div className="rounded-md border border-info-hairline bg-info-tint px-4 pb-3 pt-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <span className="type-small-medium">{t('warming.inProgress.days')}</span>
          <WarmDayCount days={days} target={target} idle={account.idle_days ?? 0} />
        </div>

        {/* day bar: days done, the day in progress, days to come */}
        <ProgressBar
          segments={DAY_SEGMENTS}
          value={days}
          max={target}
          label={t('warming.inProgress.days')}
        />
      </div>

      {!complete && !blocked ? <TodayRow account={account} /> : null}
      <VisitRow account={account} />
      {blocked ? (
        <SpamBlockPanel
          busy={busy}
          onDelete={() => {
            onDelete(account.account_id);
          }}
          onKeep={() => {
            onKeep(account.account_id);
          }}
        />
      ) : (
        <AttentionRow account={account} />
      )}

      {/* The backend decides when @SpamBot has been silent long enough to say so. */}
      {account.spam_check_warning ? (
        <div
          role="status"
          className="mt-3 flex items-center gap-2 rounded-sm border border-warning-line bg-warning-tint px-3 py-2 type-small text-warning-deep"
        >
          <Icon name="alert-triangle" size={14} className="shrink-0" />
          {t('warming.card.spamUnknown')}
        </div>
      ) : null}

      {!complete ? (
        <>
          {/* activity log */}
          <button
            type="button"
            onClick={() => {
              setOpen((v) => !v);
            }}
            className="mt-3 flex w-full items-center justify-center gap-1 border-t border-canvas pt-3 text-small text-content-muted"
          >
            {t('warming.card.logToggle')}
            <span
              className={`flex transition-transform duration-reveal ease-spring ${open ? 'rotate-180' : ''}`}
            >
              <Icon name="chevron-down" size={12} />
            </span>
          </button>
          {open ? (
            <div className="mt-3">
              {visibleLines.length > 0 ? (
                <div className="mb-2 flex justify-end">
                  <Button
                    size="sm"
                    onClick={() => {
                      setClearedAt(Date.now());
                    }}
                    className="bg-transparent text-content-muted hover:border-info-line hover:text-info-strong"
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
                  </Button>
                </div>
              ) : null}
              <TerminalPane size="inline">
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
                      <div key={line.id} className="flex gap-1">
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
              </TerminalPane>
            </div>
          ) : null}
        </>
      ) : (
        <>
          {/* complete */}
          <div className="mt-3 flex items-center gap-3 rounded-md border border-success-line bg-success-tint px-3 py-3">
            <span className="inline-flex size-chip shrink-0 items-center justify-center rounded-full bg-success">
              <Icon name="check" size={14} className="stroke-on-fill" />
            </span>
            <div className="min-w-0">
              <div className="type-body-medium text-success-deep">
                {t('warming.card.completeTitle')}
              </div>
              {/* Grey, while the heading above it is green: the green is already carried by
                  the heading, the tint, the border and the check badge, so this line is
                  supporting prose and not a third copy of the same signal. It is also the
                  only way it reaches AA — every green dark enough to pass on `success-tint`
                  is indistinguishable from the heading's `success-deep` (10.05:1 here
                  against 3.70:1 for the old literal). Do not "restore the family". */}
              <div className="type-small text-content-secondary">
                {t('warming.card.completeSub', {
                  days: t('warming.card.dayProgress', { days, target, count: target }),
                })}
              </div>
            </div>
          </div>
          <div className="mt-3 flex items-center gap-2">
            <FeedbackMark result={result} />
            <Button
              variant="primary"
              disabled={busy}
              onClick={() => {
                onPromote(account.account_id);
              }}
              // Зелёная заливка — решение места вызова, как у пяти белых `danger`: у
              // `VARIANT` нет залитого успеха, и одного носителя для имени мало.
              className="flex-1 shrink gap-2 bg-success-deep hover:bg-success-press"
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
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

// The design's "Warming" panel: blue-tinted in-progress cards, each with the
// phase/trust/spam row, worked-day bar, today/next-visit/DM row, the one problem row,
// expandable terminal log, and completion state.
export function WarmingBoard({
  warming,
  onStop,
  onPromote,
  onDelete = noop,
  onKeep = noop,
  busyIds,
  feedback = {},
  logLimit = DEFAULT_CARD_LOG_LIMIT,
  channelLabels = {},
  defaultTargetDays = TWO_WEEK_DAYS,
}: WarmingBoardProps) {
  const { t } = useTranslation();
  return (
    <Card className="p-4">
      <CardHeader
        className="mb-4"
        title={t('warming.inProgress.title')}
        icon={
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M3 12h4l3 8 4-16 3 8h4" />
          </svg>
        }
      >
        {warming.length > 0 ? (
          <Badge size="sm" tone="success" className="tb-pulse">
            {t('warming.inProgress.live')}
          </Badge>
        ) : null}
      </CardHeader>

      <div className="grid grid-cols-1 items-start gap-3 sm:grid-cols-[repeat(auto-fill,minmax(320px,1fr))]">
        {warming.map((account) => (
          <WarmingCard
            key={account.account_id}
            account={account}
            onStop={onStop}
            onPromote={onPromote}
            onDelete={onDelete}
            onKeep={onKeep}
            busy={busyIds.has(account.account_id)}
            result={feedback[account.account_id]}
            logLimit={logLimit}
            channelLabels={channelLabels}
            defaultTargetDays={defaultTargetDays}
          />
        ))}
        {warming.length === 0 ? (
          <div className="col-span-full rounded-md border-[1.5px] border-dashed border-info-line px-3 py-16 text-center type-body text-content-subtle">
            {t('warming.column.empty')}
          </div>
        ) : null}
      </div>
    </Card>
  );
}
