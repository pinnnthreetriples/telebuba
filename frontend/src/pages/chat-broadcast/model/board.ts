// How one board row reads: its status badge, its "when", its importance. Pure: the page
// passes `t`, the clock and the campaign, so the same row always renders the same words.
import type { TFunction } from 'i18next';

import type {
  ChatBroadcastBoard,
  ChatBroadcastBoardRow,
  ChatBroadcastHistoryEntry,
} from '@/shared/api';
import type { BadgeTone } from '@/shared/ui';

// `countdownTo` (epoch ms) makes the cell tick down to the next message instead of `when`.
export type RowView = { label: string; tone: BadgeTone; when: string; countdownTo?: number };

type Context = {
  board: ChatBroadcastBoard;
  now: number;
  time: (iso: string) => string;
  approvalHours: number;
};

const LIVE = new Set(['running', 'stopping']);

function skipLabel(t: TFunction, row: ChatBroadcastBoardRow, approvalHours: number): string {
  const reason = row.skip_reason ?? 'error';
  return t(`chatBroadcast.skip.${reason}`, { hours: approvalHours });
}

function lastSkipAt(row: ChatBroadcastBoardRow): string | null {
  return row.history.find((entry) => entry.kind === 'skipped')?.at ?? null;
}

export function rowView(t: TFunction, row: ChatBroadcastBoardRow, ctx: Context): RowView {
  const { board, now, time } = ctx;
  const status = board.campaign.status;
  if (row.state === 'skipped') {
    const at = lastSkipAt(row);
    return {
      label: t('chatBroadcast.status.skipped', { reason: skipLabel(t, row, ctx.approvalHours) }),
      tone: 'danger',
      when: at === null ? '—' : t('chatBroadcast.when.skippedAt', { time: time(at) }),
    };
  }
  if (row.state === 'done' || (status === 'done' && row.state === 'round_done')) {
    return {
      label: t('chatBroadcast.status.done'),
      tone: 'success',
      when:
        row.last_sent_at === null || row.last_sent_at === undefined
          ? t('chatBroadcast.when.done')
          : t('chatBroadcast.when.sentAt', { time: time(row.last_sent_at) }),
    };
  }
  if (!LIVE.has(status) && status !== 'stalled' && status !== 'draft') {
    return {
      label: t('chatBroadcast.status.paused'),
      tone: 'neutral',
      when: t('chatBroadcast.when.paused'),
    };
  }
  const next = row.next_action_at ?? null;
  switch (row.state) {
    case 'writing':
      return {
        label: t('chatBroadcast.status.writing'),
        tone: 'success',
        countdownTo: next !== null && Date.parse(next) > now ? Date.parse(next) : undefined,
        when:
          row.last_sent_at === null || row.last_sent_at === undefined
            ? t('chatBroadcast.when.joiningNow')
            : t('chatBroadcast.when.sentAt', { time: time(row.last_sent_at) }),
      };
    case 'waiting':
      if (next !== null && Date.parse(next) > now) {
        return {
          label: t('chatBroadcast.status.waitingUntil', { time: time(next) }),
          tone: 'warning',
          when: t('chatBroadcast.when.writesAt', { time: time(next) }),
        };
      }
      return {
        label: t('chatBroadcast.status.queued'),
        tone: 'neutral',
        when: t('chatBroadcast.when.queued'),
      };
    case 'pending_approval':
      return {
        label: t('chatBroadcast.status.pending_approval'),
        tone: 'warning',
        when:
          row.requested_at === null || row.requested_at === undefined
            ? '—'
            : t('chatBroadcast.when.requestedAt', { time: time(row.requested_at) }),
      };
    case 'joining':
    case 'captcha':
      return {
        label: t(`chatBroadcast.status.${row.state}`),
        tone: 'info',
        when: t('chatBroadcast.when.joiningNow'),
      };
    case 'reconnecting':
      return {
        label: t('chatBroadcast.status.reconnecting'),
        tone: 'info',
        when: t('chatBroadcast.when.reconnect'),
      };
    case 'waiting_account':
      return {
        label: t('chatBroadcast.status.waiting_account'),
        tone: 'warning',
        when: t('chatBroadcast.when.needAccount'),
      };
    case 'round_done': {
      const rest = board.campaign.rest_until ?? null;
      return rest === null
        ? { label: t('chatBroadcast.status.roundDone'), tone: 'neutral', when: '—' }
        : {
            label: t('chatBroadcast.status.restUntil', { time: time(rest) }),
            tone: 'neutral',
            when: t('chatBroadcast.when.nextRound', { round: row.round + 1, time: time(rest) }),
          };
    }
    default:
      return {
        label: t('chatBroadcast.status.queued'),
        tone: 'neutral',
        when: t('chatBroadcast.when.queued'),
      };
  }
}

// Top: what needs the operator; then where messages are going out; then joins; then
// waits; queue and rest last.
export function priority(row: ChatBroadcastBoardRow): number {
  switch (row.state) {
    case 'captcha':
    case 'reconnecting':
    case 'waiting_account':
      return 0;
    case 'writing':
      return 1;
    case 'joining':
      return 2;
    case 'waiting':
    case 'pending_approval':
      return 3;
    default:
      return 4;
  }
}

export type GroupId = 'attention' | 'writing' | 'joining' | 'waiting' | 'rest' | 'ok' | 'skipped';

export const ACTIVE_GROUPS: GroupId[] = ['attention', 'writing', 'joining', 'waiting', 'rest'];
export const DONE_GROUPS: GroupId[] = ['ok', 'skipped'];

export function inGroup(row: ChatBroadcastBoardRow, group: GroupId): boolean {
  switch (group) {
    case 'ok':
      return row.state !== 'skipped';
    case 'skipped':
      return row.state === 'skipped';
    default:
      return ACTIVE_GROUPS.indexOf(group) === priority(row);
  }
}

// The order is fixed once, when the board first loads: a row the operator just acted on
// must not slide away from under the cursor because its status changed.
export function orderOf(rows: ChatBroadcastBoardRow[]): string[] {
  return [...rows].sort((a, b) => priority(a) - priority(b)).map((row) => row.chat_key);
}

export function applyOrder(
  rows: ChatBroadcastBoardRow[],
  order: string[],
): ChatBroadcastBoardRow[] {
  const rank = new Map(order.map((key, index) => [key, index]));
  return [...rows].sort(
    (a, b) =>
      (rank.get(a.chat_key) ?? order.length + priority(a)) -
      (rank.get(b.chat_key) ?? order.length + priority(b)),
  );
}

export function allResting(rows: ChatBroadcastBoardRow[]): boolean {
  return rows.length > 0 && rows.every((row) => row.state === 'round_done');
}

export type HistoryLine = {
  text: string;
  tone: 'sent' | 'waiting' | 'handed' | 'skipped' | 'deleted';
};

export function historyLine(
  t: TFunction,
  entry: ChatBroadcastHistoryEntry,
  ctx: { time: (iso: string) => string; nameOf: (id: string) => string; approvalHours: number },
): HistoryLine {
  switch (entry.kind) {
    case 'sent':
      return { text: entry.text ?? '', tone: 'sent' };
    case 'unconfirmed':
      return {
        text: t('chatBroadcast.history.unconfirmed', { text: entry.text ?? '' }),
        tone: 'sent',
      };
    case 'deleted':
      // A journal row carries the deleted text (struck through); the history event of
      // the check that found it carries none.
      return entry.text === null || entry.text === undefined
        ? { text: t('chatBroadcast.history.deleted'), tone: 'skipped' }
        : { text: entry.text, tone: 'deleted' };
    case 'failed':
      return {
        text: t('chatBroadcast.history.failed', { reason: entry.detail ?? '' }),
        tone: 'skipped',
      };
    case 'joined': {
      const due = Number(entry.detail ?? 0) * 1000;
      const now = Date.parse(entry.at);
      return due > now
        ? {
            text: t('chatBroadcast.history.joinedLater', {
              time: ctx.time(new Date(due).toISOString()),
            }),
            tone: 'waiting',
          }
        : { text: t('chatBroadcast.history.joinedNow'), tone: 'waiting' };
    }
    case 'requested':
      return {
        text: t('chatBroadcast.history.requested', { hours: ctx.approvalHours }),
        tone: 'waiting',
      };
    case 'handed':
      return {
        text:
          entry.detail === null || entry.detail === undefined
            ? t('chatBroadcast.history.handedFresh')
            : t('chatBroadcast.history.handed', { name: ctx.nameOf(entry.detail) }),
        tone: 'handed',
      };
    case 'skipped':
      return {
        text: t('chatBroadcast.history.skipped', {
          reason: t(`chatBroadcast.skip.${entry.detail ?? 'error'}`, { hours: ctx.approvalHours }),
        }),
        tone: 'skipped',
      };
    default:
      return { text: t(`chatBroadcast.history.${entry.kind}`), tone: 'waiting' };
  }
}
