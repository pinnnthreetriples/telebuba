// The pipeline card, read off the board and the saved settings: the badge, the one main
// button, the stages, the notice and the tiles. Pure — the page passes `t`, the clock and
// a time formatter; the wording is the mockup's (`campaigns.ts → pipeline`).
import type { TFunction } from 'i18next';

import type { MasstaggingBoard, MasstaggingCampaign } from '@/shared/api';
import type { BadgeTone, StatTone, StepperStep } from '@/shared/ui';

import type { Settings } from './draft';

type Status = MasstaggingCampaign['status'];
type NoticeTone = 'info' | 'success' | 'warning' | 'danger';

export type PipelineAction = 'start' | 'pause' | 'resume' | 'restart';

export type PipelineView = {
  badge: { label: string; tone: BadgeTone };
  action: { kind: PipelineAction; label: string; disabled: boolean };
  steps: StepperStep[];
  notice: { tone: NoticeTone; text: string };
  stats: { label: string; value: number | string; tone?: StatTone }[];
  started: boolean;
  progress: { done: number; total: number };
};

export const STATUS_TONE: Record<Status, BadgeTone> = {
  draft: 'neutral',
  ready: 'neutral',
  running: 'info',
  paused: 'warning',
  resting: 'info',
  done: 'success',
  failed: 'danger',
};

const LIVE: Status[] = ['running', 'resting'];
const SECOND_MS = 1000;
const MINUTE_MS = 60 * SECOND_MS;

type Inputs = {
  board: MasstaggingBoard;
  settings: Settings;
  now: number;
  time: (iso: string) => string;
};

function actionOf(t: TFunction, status: Status, missing: number): PipelineView['action'] {
  if (LIVE.includes(status)) {
    return { kind: 'pause', label: t('masstagging.pipeline.pause'), disabled: false };
  }
  if (status === 'paused' || status === 'failed') {
    return { kind: 'resume', label: t('masstagging.pipeline.resume'), disabled: missing > 0 };
  }
  if (status === 'done') {
    return { kind: 'restart', label: t('masstagging.pipeline.restart'), disabled: missing > 0 };
  }
  return { kind: 'start', label: t('masstagging.pipeline.start'), disabled: status === 'draft' };
}

function stepsOf(t: TFunction, inputs: Inputs): StepperStep[] {
  const { board, settings } = inputs;
  const status = board.campaign.status;
  const story = settings.mode === 'stories';
  const counters = board.counters;
  const ids: string[] = story
    ? ['accounts', 'targets', ...(settings.prime_first ? ['dialog'] : []), 'story', 'done']
    : ['accounts', 'targets', 'toggle', 'done'];
  const captions: Record<string, string> = {
    accounts:
      counters.excluded_accounts > 0
        ? t('masstagging.pipeline.sub.accountsExcluded', {
            count: counters.working_accounts,
            excluded: counters.excluded_accounts,
          })
        : String(counters.working_accounts),
    targets: String(counters.total),
    dialog: t('masstagging.pipeline.sub.dialog'),
    story: t('masstagging.pipeline.sub.story', { count: settings.mentions_per_story }),
    toggle: t('masstagging.pipeline.sub.toggle', { count: settings.dialogs_per_day }),
    done: '',
  };
  const work = 2;
  return ids.map((id, index) => {
    let state: StepperStep['state'] = 'upcoming';
    if (status === 'done') state = 'done';
    else if (status === 'draft' || status === 'ready') {
      const met = index === 0 ? board.campaign.account_count > 0 : counters.total > 0;
      if (index < work && met) state = 'done';
    } else if (index < work) state = 'done';
    else if (index === work || (status === 'running' && index < ids.length - 1)) {
      state = 'current';
    }
    return {
      id,
      label: t(`masstagging.pipeline.step.${id}`),
      caption: captions[id] || undefined,
      state,
    };
  });
}

function nextIn(t: TFunction, inputs: Inputs): string | null {
  const at = inputs.board.next_action_at;
  if (at === null || at === undefined) return null;
  const left = Date.parse(at) - inputs.now;
  if (Number.isNaN(left) || left <= 0) return null;
  return left >= MINUTE_MS
    ? t('masstagging.pipeline.inMinutes', { count: Math.round(left / MINUTE_MS) })
    : t('masstagging.pipeline.inSeconds', { count: Math.max(1, Math.round(left / SECOND_MS)) });
}

function failedText(t: TFunction, error: string | null | undefined): string {
  if (error === 'floods') return t('masstagging.pipeline.notice.failedFloods');
  if (error === 'no_accounts') return t('masstagging.pipeline.notice.failedAccounts');
  return t('masstagging.pipeline.notice.failed');
}

function noticeOf(t: TFunction, inputs: Inputs): PipelineView['notice'] {
  const { board, settings } = inputs;
  const campaign = board.campaign;
  const counted = { tagged: board.counters.tagged, total: board.counters.total };
  switch (campaign.status) {
    case 'draft': {
      const missing = campaign.missing.map((code) => t(`masstagging.missing.${code}`));
      return {
        tone: 'warning',
        text: t('masstagging.pipeline.notice.draft', { missing: missing.join(', ') }),
      };
    }
    case 'ready':
      return { tone: 'success', text: t('masstagging.pipeline.notice.ready') };
    case 'running': {
      const next = nextIn(t, inputs);
      if (next === null) return { tone: 'info', text: t('masstagging.pipeline.notice.running') };
      const key = settings.mode === 'stories' ? 'runningStory' : 'runningDialog';
      return { tone: 'info', text: t(`masstagging.pipeline.notice.${key}`, { next }) };
    }
    case 'paused':
      return {
        tone: 'warning',
        text: t(
          campaign.last_error === 'volume'
            ? 'masstagging.pipeline.notice.pausedVolume'
            : 'masstagging.pipeline.notice.paused',
          counted,
        ),
      };
    case 'resting':
      return {
        tone: 'info',
        text: t('masstagging.pipeline.notice.resting', {
          time: campaign.rest_until ? inputs.time(campaign.rest_until) : '09:00',
        }),
      };
    case 'done':
      return { tone: 'success', text: t('masstagging.pipeline.notice.done', counted) };
    case 'failed':
      return { tone: 'danger', text: failedText(t, campaign.last_error) };
  }
}

export function pipelineView(t: TFunction, inputs: Inputs): PipelineView {
  const { board } = inputs;
  const status = board.campaign.status;
  const counters = board.counters;
  const days = counters.days_left;
  return {
    badge: { label: t(`masstagging.status.${status}`), tone: STATUS_TONE[status] },
    action: actionOf(t, status, board.campaign.missing.length),
    steps: stepsOf(t, inputs),
    notice: noticeOf(t, inputs),
    stats: [
      {
        label: t('masstagging.pipeline.stat.tagged'),
        value: t('masstagging.pipeline.stat.taggedOf', {
          tagged: counters.tagged,
          total: counters.total,
        }),
        tone: 'success',
      },
      { label: t('masstagging.pipeline.stat.left'), value: counters.left, tone: 'primary' },
      {
        label: t('masstagging.pipeline.stat.failed'),
        value: counters.failed,
        tone: counters.failed > 0 ? 'danger' : 'default',
      },
      {
        label:
          days === null || days === undefined
            ? t('masstagging.pipeline.stat.perDay')
            : t('masstagging.pipeline.stat.perDayLeft', { count: days }),
        value: counters.per_day,
      },
    ],
    started: status !== 'draft' && status !== 'ready',
    progress: { done: counters.total - counters.left, total: counters.total },
  };
}
