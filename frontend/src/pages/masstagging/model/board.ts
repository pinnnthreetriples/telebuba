// The board by person: the two tabs, their order (fixed once per load, so a row the
// operator acts on does not slide away), the «Когда» cell and the history lines. Pure.
import type { TFunction } from 'i18next';

import type { MasstaggingBoardRow, MasstaggingCampaign } from '@/shared/api';
import type { BadgeTone } from '@/shared/ui';

type Status = MasstaggingCampaign['status'];
type RowState = MasstaggingBoardRow['state'];
type History = MasstaggingBoardRow['history'][number];

export type HistoryTone = 'muted' | 'info' | 'warning' | 'danger';

export const ROW_TONE: Record<RowState, BadgeTone> = {
  queued: 'neutral',
  primed: 'info',
  working: 'info',
  tagged: 'success',
  skipped: 'neutral',
  failed: 'danger',
  flood: 'warning',
};

// The "in work" tab, most urgent first: what an account holds now, who waits for its
// story, who waits for an account, then the queue.
const ACTIVE_ORDER: RowState[] = ['working', 'primed', 'flood', 'queued'];

export function splitRows(rows: MasstaggingBoardRow[]): {
  active: MasstaggingBoardRow[];
  done: MasstaggingBoardRow[];
} {
  const active = rows
    .filter((row) => row.active)
    .map((row, index) => ({ row, index }))
    .sort(
      (a, b) =>
        ACTIVE_ORDER.indexOf(a.row.state) - ACTIVE_ORDER.indexOf(b.row.state) || a.index - b.index,
    )
    .map(({ row }) => row);
  const done = rows
    .filter((row) => !row.active)
    .map((row, index) => ({ row, index }))
    .sort(
      (a, b) =>
        (Date.parse(b.row.when ?? '') || 0) - (Date.parse(a.row.when ?? '') || 0) ||
        a.index - b.index,
    )
    .map(({ row }) => row);
  return { active, done };
}

// The tab a board opens on: a finished campaign shows what got done.
export function defaultTab(status: Status, active: number, done: number): 'active' | 'done' {
  if (status === 'done' && done > 0) return 'done';
  return active === 0 && done > 0 ? 'done' : 'active';
}

export function whenText(
  t: TFunction,
  row: MasstaggingBoardRow,
  status: Status,
  time: (iso: string) => string,
): string {
  switch (row.state) {
    case 'queued':
      if (status === 'resting') return t('masstagging.board.when.tomorrow');
      return row.when ? time(row.when) : '—';
    case 'flood':
      return row.when
        ? t('masstagging.board.when.afterPauseAt', { time: time(row.when) })
        : t('masstagging.board.when.afterPause');
    case 'primed':
      return t('masstagging.board.when.nextStory');
    case 'working':
      return t('masstagging.board.when.now');
    default:
      return row.when ? time(row.when) : '—';
  }
}

export function historyTone(entry: History): HistoryTone {
  switch (entry.event) {
    case 'failed':
      return 'danger';
    case 'flood':
      return 'warning';
    case 'now':
    case 'requeued':
      return 'info';
    default:
      return 'muted';
  }
}

export function historyText(t: TFunction, entry: History, stories: boolean): string {
  const detail = entry.detail ?? '';
  switch (entry.event) {
    case 'tagged':
      return stories
        ? t('masstagging.board.history.taggedStory', { count: Number(detail) || 1 })
        : t('masstagging.board.history.taggedPriming');
    case 'skipped':
    case 'failed':
      return t(`masstagging.board.history.${entry.event}`, {
        reason: t(`masstagging.reason.${detail}`, { defaultValue: detail }),
      });
    case 'flood':
      return t('masstagging.board.history.flood', { seconds: detail });
    case 'expanded':
      return t('masstagging.board.history.expanded', { count: Number(detail) || 0 });
    default:
      return t(`masstagging.board.history.${entry.event}`);
  }
}

// A row the operator can act on, and with what: tag (or prime) now and skip for anyone
// still waiting, back to the queue for what failed or was skipped. A row an account is
// working on right now takes nothing.
export function rowActions(row: MasstaggingBoardRow): ('now' | 'skip' | 'requeue')[] {
  if (row.state === 'queued' || row.state === 'primed' || row.state === 'flood') {
    return ['now', 'skip'];
  }
  if (row.state === 'failed' || row.state === 'skipped') return ['requeue'];
  return [];
}
