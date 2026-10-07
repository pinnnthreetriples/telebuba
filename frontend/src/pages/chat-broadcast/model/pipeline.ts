// The pipeline card, read off the board and the saved settings: the badge, the one main
// button, the five nodes, the notice and the counters. Pure — the page passes `t`, the
// clock and an account-name lookup.
import type { TFunction } from 'i18next';

import type { ChatBroadcastBoard } from '@/shared/api';
import type { BadgeTone } from '@/shared/ui';

import type { Settings } from './draft';

type NoticeTone = 'info' | 'success' | 'warning' | 'danger';
type ChatBroadcastPhase = ChatBroadcastBoard['phase'];

export type PipelineAction = 'start' | 'stop' | 'resume' | 'restart';

export type PipelineView = {
  badge: { label: string; tone: BadgeTone };
  action: { kind: PipelineAction; label: string; disabled: boolean };
  nodes: { id: string; label: string; sub: string; done: boolean }[];
  notice: { tone: NoticeTone; text: string };
  extras: { tone: NoticeTone; text: string }[];
  stats: { id: string; label: string; value: number }[];
  progress: { sent: number; total: number | null } | null;
  chips: { label: string; tone: BadgeTone }[];
};

const BADGE_TONE: Record<ChatBroadcastPhase, BadgeTone> = {
  draft: 'neutral',
  joining: 'info',
  running: 'info',
  resting: 'neutral',
  stopping: 'neutral',
  stopped: 'neutral',
  done: 'success',
  failed: 'danger',
  stalled: 'danger',
};

const LIVE: ChatBroadcastPhase[] = ['joining', 'running', 'resting', 'stopping'];

export function delayLabel(t: TFunction, minutes: number): string {
  return t(`chatBroadcast.settings.pace.delay${String(minutes)}`);
}

type Inputs = {
  board: ChatBroadcastBoard;
  settings: Settings;
  // What the settings dialog would refuse to launch; empty when ready.
  missing: string[];
  time: (iso: string) => string;
  nameOf: (id: string) => string;
};

function actionOf(t: TFunction, phase: ChatBroadcastPhase, inputs: Inputs): PipelineView['action'] {
  if (LIVE.includes(phase)) {
    return {
      kind: 'stop',
      label: t('chatBroadcast.pipeline.stop'),
      disabled: phase === 'stopping',
    };
  }
  if (phase === 'done') {
    return {
      kind: 'restart',
      label: t('chatBroadcast.pipeline.restart'),
      disabled: inputs.missing.length > 0,
    };
  }
  if (phase === 'draft') {
    return {
      kind: 'start',
      label: t('chatBroadcast.pipeline.start'),
      disabled: inputs.missing.length > 0,
    };
  }
  const anyFree = inputs.board.accounts.some((account) => account.state === 'active');
  return {
    kind: 'resume',
    label: t('chatBroadcast.pipeline.resume'),
    disabled: !anyFree || inputs.missing.length > 0,
  };
}

function nodesOf(t: TFunction, inputs: Inputs): PipelineView['nodes'] {
  const { board, settings } = inputs;
  const counters = board.counters;
  const own = settings.target_mode === 'own';
  const chain =
    board.chain_length === 0
      ? t('chatBroadcast.pipeline.sub.noChain')
      : t('chatBroadcast.pipeline.sub.chain', { count: board.chain_length });
  const ai = settings.first_message === 'ai' || settings.randomize;
  const started = board.phase !== 'draft';
  const rounds = counters.rounds ?? null;
  const round = Math.max(1, board.campaign.round);
  let roundSub: string;
  if (!started) {
    roundSub =
      rounds === null
        ? t('chatBroadcast.pipeline.sub.endless')
        : t('chatBroadcast.pipeline.sub.roundsPlanned', { count: rounds });
  } else if (board.phase === 'done' && rounds !== null) {
    roundSub = t('chatBroadcast.pipeline.sub.roundsDone', { rounds });
  } else {
    roundSub =
      rounds === null
        ? t('chatBroadcast.pipeline.sub.roundEndless', { round })
        : t('chatBroadcast.pipeline.sub.round', { round, rounds });
  }
  let joinSub =
    settings.join_delay_minutes === 0
      ? t('chatBroadcast.pipeline.sub.joinNow')
      : t('chatBroadcast.pipeline.sub.joinAfter', {
          delay: delayLabel(t, settings.join_delay_minutes).toLowerCase(),
        });
  if (board.phase === 'joining') {
    joinSub = t('chatBroadcast.pipeline.sub.joined', {
      joined: counters.joined,
      chats: counters.chats,
    });
  } else if (started && counters.chats > 0 && counters.joined >= counters.chats) {
    joinSub = t('chatBroadcast.pipeline.sub.allJoined');
  }
  let chatsSub: string;
  if (counters.chats > 0) {
    chatsSub = t('chatBroadcast.pipeline.sub.chats', { count: counters.chats });
  } else if (own) {
    chatsSub = t('chatBroadcast.pipeline.sub.ownChats');
  } else {
    chatsSub =
      settings.targets.length > 0
        ? t('chatBroadcast.pipeline.sub.chats', { count: settings.targets.length })
        : t('chatBroadcast.pipeline.sub.noChats');
  }
  return [
    {
      id: 'accounts',
      label: t('chatBroadcast.pipeline.nodes.accounts'),
      sub:
        counters.accounts === 0
          ? t('chatBroadcast.pipeline.sub.noAccounts')
          : t('chatBroadcast.pipeline.sub.accounts', {
              working: counters.accounts_working,
              total: counters.accounts,
            }),
      done: counters.accounts_working > 0,
    },
    {
      id: 'chats',
      label: t('chatBroadcast.pipeline.nodes.chats'),
      sub: chatsSub,
      done: counters.chats > 0 || own || settings.targets.length > 0,
    },
    {
      id: 'join',
      label: t('chatBroadcast.pipeline.nodes.join'),
      sub: joinSub,
      done: started && board.phase !== 'joining',
    },
    {
      id: 'chain',
      label: t('chatBroadcast.pipeline.nodes.chain'),
      sub: ai && board.chain_length > 0 ? t('chatBroadcast.pipeline.sub.ai', { chain }) : chain,
      done: board.chain_length > 0,
    },
    {
      id: 'rounds',
      label: t('chatBroadcast.pipeline.nodes.rounds'),
      sub: roundSub,
      done: board.phase === 'done' || board.phase === 'resting',
    },
  ];
}

function noticeOf(t: TFunction, inputs: Inputs): PipelineView['notice'] {
  const { board, settings } = inputs;
  const counters = board.counters;
  const campaign = board.campaign;
  switch (board.phase) {
    case 'draft':
      return inputs.missing.length > 0
        ? {
            tone: 'info',
            text: t('chatBroadcast.pipeline.notice.draft', { missing: inputs.missing.join(', ') }),
          }
        : { tone: 'info', text: t('chatBroadcast.pipeline.notice.draftReady') };
    case 'joining':
      return {
        tone: 'info',
        text: t('chatBroadcast.pipeline.notice.joining', {
          joined: counters.joined,
          pending: counters.pending_approval,
          delay: delayLabel(t, settings.join_delay_minutes).toLowerCase(),
        }),
      };
    case 'running':
      return {
        tone: 'success',
        text: t('chatBroadcast.pipeline.notice.running', { waiting: counters.waiting }),
      };
    case 'resting':
      return {
        tone: 'info',
        text: t('chatBroadcast.pipeline.notice.resting', {
          round: campaign.round,
          next: campaign.round + 1,
          time: campaign.rest_until ? inputs.time(campaign.rest_until) : '—',
        }),
      };
    case 'stopped':
      return {
        tone: 'info',
        text: t('chatBroadcast.pipeline.notice.stopped', {
          time: board.finished_at ? inputs.time(board.finished_at) : '—',
        }),
      };
    case 'done':
      return {
        tone: 'success',
        text: t('chatBroadcast.pipeline.notice.done', {
          sent: counters.sent,
          chats: counters.chats,
          skipped: counters.skipped,
        }),
      };
    case 'failed':
      return {
        tone: 'danger',
        text: t('chatBroadcast.pipeline.notice.failed', { error: campaign.last_error ?? '—' }),
      };
    case 'stalled':
      return { tone: 'danger', text: t('chatBroadcast.pipeline.notice.stalled') };
    default:
      return { tone: 'info', text: t('chatBroadcast.pipeline.notice.stopping') };
  }
}

export function pipelineView(t: TFunction, inputs: Inputs): PipelineView {
  const { board } = inputs;
  const counters = board.counters;
  const halted = board.accounts.filter((account) => account.state === 'halted');
  const busy = board.accounts.filter((account) => account.state === 'busy');
  const extras: PipelineView['extras'] = [];
  if (halted.length > 0 && board.phase !== 'draft') {
    extras.push({
      tone: 'warning',
      text: t('chatBroadcast.pipeline.notice.halted', {
        names: halted.map((account) => inputs.nameOf(account.account_id)).join(', '),
      }),
    });
  }
  if (busy.length > 0) {
    extras.push({
      tone: 'info',
      text: t('chatBroadcast.pipeline.notice.busy', {
        names: busy
          .map(
            (account) =>
              `${inputs.nameOf(account.account_id)} (${t(`chatBroadcast.busy.${account.busy_owner ?? 'warming'}`)})`,
          )
          .join(', '),
      }),
    });
  }
  const chips: PipelineView['chips'] = [];
  if (counters.handed > 0) {
    chips.push({
      label: t('chatBroadcast.pipeline.handed', { count: counters.handed }),
      tone: 'neutral',
    });
  }
  if (board.resumed_at) {
    chips.push({
      label: t('chatBroadcast.pipeline.resumed', { time: inputs.time(board.resumed_at) }),
      tone: 'info',
    });
  }
  const pendingLabel =
    board.phase === 'joining'
      ? {
          id: 'pending',
          label: t('chatBroadcast.pipeline.stats.waitingApproval'),
          value: counters.pending_approval,
        }
      : {
          id: 'waiting',
          label: t('chatBroadcast.pipeline.stats.waitingJoin'),
          value: counters.waiting,
        };
  return {
    badge: { label: t(`chatBroadcast.phase.${board.phase}`), tone: BADGE_TONE[board.phase] },
    action: actionOf(t, board.phase, inputs),
    nodes: nodesOf(t, inputs),
    notice: noticeOf(t, inputs),
    extras,
    stats: [
      {
        id: 'accounts',
        label: t('chatBroadcast.pipeline.stats.accounts'),
        value: counters.accounts,
      },
      { id: 'chats', label: t('chatBroadcast.pipeline.stats.chats'), value: counters.chats },
      { id: 'sent', label: t('chatBroadcast.pipeline.stats.sent'), value: counters.sent },
      pendingLabel,
      { id: 'skipped', label: t('chatBroadcast.pipeline.stats.skipped'), value: counters.skipped },
    ],
    progress:
      board.phase === 'draft' ? null : { sent: counters.sent, total: counters.planned ?? null },
    chips,
  };
}
