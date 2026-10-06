// The "check how it will go" scenario, built from the dialog's draft: one account's way
// through two example chats with a clock that starts at 14:00, then round 2. Every number
// is the draft's own — pauses, the wait after a join, the approval window, rounds, the
// stop — so what the operator confirms is what Save writes.
import type { TFunction } from 'i18next';

import type { IconName } from '@/shared/ui';

import type { Draft, MessageDraft } from './draft';
import { isFilled, splitTargets, VARIANTS } from './draft';

export type StepTone = 'join' | 'wait' | 'msg' | 'done';
export type Step = {
  label: string;
  tone: StepTone;
  icon: IconName;
  time: string;
  note?: string;
  bubble?: { text: string; photo: boolean; post: boolean };
};
export type Block =
  | { kind: 'chat'; id: string; title: string; caption: string; icon: IconName; steps: Step[] }
  | { kind: 'divider'; id: string; label: string; strong?: boolean };

// The example's admin accepts a request after this many minutes.
const APPROVED_AFTER = 40;
const START_MINUTES = 14 * 60;
const ROUND_TWO_AT = 4 * 60;

export function clock(minutes: number): string {
  const total = START_MINUTES + minutes;
  return `${String(Math.floor(total / 60) % 24)}:${String(total % 60).padStart(2, '0')}`;
}

// Picks variant `pick` of every {a|b} and fills the chat variables, as the bot would.
export function resolveText(text: string, pick: number, chat: string): string {
  let rendered = text;
  let guard = 0;
  while (VARIANTS.test(rendered) && guard < 50) {
    rendered = rendered.replace(/\{([^{}]*\|[^{}]*)\}/, (_whole, body: string) => {
      const options = body.split('|');
      return options[pick % options.length] ?? '';
    });
    guard += 1;
  }
  return rendered.replaceAll('{group_title}', chat).replaceAll('{group_username}', chat);
}

export function filledMessages(draft: Draft): MessageDraft[] {
  return draft.messages.filter((message, index) => isFilled(message, draft, index));
}

type Scene = { t: TFunction; draft: Draft; ownTitles: string[] };

function range(t: TFunction, bounds: { min: number; max: number }, unit: 'Sec' | 'Min'): string {
  return t(`chatBroadcast.preview.range${unit}`, { min: bounds.min, max: bounds.max });
}

function messageSteps(scene: Scene, pick: number, chat: string, at: number): Step[] {
  const { t, draft } = scene;
  const steps: Step[] = [];
  filledMessages(draft).forEach((message, index) => {
    if (index > 0) {
      steps.push({
        label: t('chatBroadcast.preview.pause', {
          range: range(t, draft.settings.between_messages, 'Sec'),
        }),
        tone: 'wait',
        icon: 'clock',
        time: clock(at),
      });
    }
    if (message.kind === 'post') {
      steps.push({
        label: t('chatBroadcast.preview.forward'),
        tone: 'msg',
        icon: 'send',
        time: clock(at),
        bubble: { text: message.post, photo: false, post: true },
      });
      return;
    }
    const aiWrites = index === 0 && draft.settings.first_message === 'ai';
    steps.push({
      label:
        message.photo === null
          ? t('chatBroadcast.preview.message', { index: index + 1 })
          : t('chatBroadcast.preview.messagePhoto', { index: index + 1 }),
      tone: 'msg',
      icon: 'send',
      time: clock(at),
      bubble: {
        text: aiWrites
          ? t('chatBroadcast.preview.aiWrites')
          : resolveText(message.text, pick, chat),
        photo: message.photo !== null,
        post: false,
      },
    });
  });
  return steps;
}

function waitAfterJoin(scene: Scene, from: number): Step[] {
  const { t, draft } = scene;
  const delay = draft.settings.join_delay_minutes;
  if (delay === 0) return [];
  return [
    {
      label: t('chatBroadcast.preview.waits', {
        delay: t(`chatBroadcast.settings.pace.delay${String(delay)}`).toLowerCase(),
      }),
      tone: 'wait',
      icon: 'clock',
      time: `${clock(from)}–${clock(from + delay)}`,
      note: t('chatBroadcast.preview.waitNote'),
    },
  ];
}

function done(scene: Scene, at: number): Step {
  return {
    label: scene.t('chatBroadcast.preview.done'),
    tone: 'done',
    icon: 'check-circle',
    time: clock(at),
  };
}

function betweenChats(scene: Scene, id: string): Block {
  return {
    kind: 'divider',
    id,
    label: scene.t('chatBroadcast.preview.pause', {
      range: range(scene.t, scene.draft.settings.between_chats, 'Sec'),
    }),
  };
}

function listBlocks(scene: Scene): Block[] {
  const { t, draft } = scene;
  const targets = splitTargets(draft.settings.targets.join(' '));
  const open = targets.find((value) => !value.includes('+') && !value.includes('addlist'));
  const closed = targets.find((value) => value.includes('+') || value.includes('joinchat'));
  const delay = draft.settings.join_delay_minutes;
  const blocks: Block[] = [];
  if (open !== undefined) {
    blocks.push({
      kind: 'chat',
      id: `open-${open}`,
      title: open,
      caption: t('chatBroadcast.preview.openChat'),
      icon: 'globe',
      steps: [
        { label: t('chatBroadcast.preview.join'), tone: 'join', icon: 'user-plus', time: clock(0) },
        ...waitAfterJoin(scene, 0),
        ...messageSteps(scene, 0, open, delay),
        done(scene, delay),
      ],
    });
  }
  if (closed !== undefined) {
    if (blocks.length > 0) blocks.push(betweenChats(scene, 'pause-1'));
    blocks.push({
      kind: 'chat',
      id: `closed-${closed}`,
      title: closed,
      caption: t('chatBroadcast.preview.closedChat'),
      icon: 'shield-check',
      steps: [
        {
          label: t('chatBroadcast.preview.request'),
          tone: 'join',
          icon: 'user-plus',
          time: clock(2),
        },
        {
          label: t('chatBroadcast.preview.awaitApproval'),
          tone: 'wait',
          icon: 'clock',
          time: `${clock(2)}–${clock(APPROVED_AFTER)}`,
          note: t('chatBroadcast.preview.approvalNote', {
            hours: draft.settings.approval_wait_hours,
          }),
        },
        {
          label: t('chatBroadcast.preview.approved'),
          tone: 'join',
          icon: 'check',
          time: clock(APPROVED_AFTER),
        },
        ...waitAfterJoin(scene, APPROVED_AFTER),
        ...messageSteps(scene, 1, closed, APPROVED_AFTER + delay),
        done(scene, APPROVED_AFTER + delay),
      ],
    });
  }
  return blocks;
}

function ownBlocks(scene: Scene): Block[] {
  const blocks: Block[] = [];
  scene.ownTitles.slice(0, 2).forEach((title, index) => {
    if (index > 0) blocks.push(betweenChats(scene, `pause-${String(index)}`));
    blocks.push({
      kind: 'chat',
      id: `own-${title}`,
      title,
      caption: scene.t('chatBroadcast.preview.ownChat'),
      icon: 'users',
      steps: [...messageSteps(scene, index, title, index), done(scene, index)],
    });
  });
  return blocks;
}

export function roundsOf(draft: Draft): number | null {
  if (!draft.settings.loop) return 1;
  return draft.settings.rounds === 0 ? null : draft.settings.rounds;
}

function roundTwo(scene: Scene, names: string[]): Block[] {
  const { t, draft } = scene;
  const rounds = roundsOf(draft);
  const steps: Step[] = [];
  names.forEach((name, index) => {
    const at = ROUND_TWO_AT + index;
    if (index > 0) {
      steps.push({
        label: t('chatBroadcast.preview.pause', {
          range: range(t, draft.settings.between_chats, 'Sec'),
        }),
        tone: 'wait',
        icon: 'clock',
        time: clock(at),
        note: t('chatBroadcast.preview.betweenAsRound1'),
      });
    }
    for (const step of messageSteps(scene, 2 + index, name, at)) {
      steps.push(
        step.tone === 'msg' ? { ...step, label: `${name} · ${step.label.toLowerCase()}` } : step,
      );
    }
  });
  const own = draft.settings.target_mode === 'own';
  return [
    {
      kind: 'divider',
      id: 'rest',
      label: t('chatBroadcast.preview.roundRest', {
        range: range(t, draft.settings.rest_minutes, 'Min'),
      }),
      strong: true,
    },
    {
      kind: 'chat',
      id: 'round-2',
      title:
        rounds === null
          ? t('chatBroadcast.preview.round2Endless')
          : t('chatBroadcast.preview.round2', { rounds }),
      caption: t('chatBroadcast.preview.round2Caption'),
      icon: 'refresh',
      steps: [...steps, done(scene, ROUND_TWO_AT + names.length)],
    },
    {
      kind: 'divider',
      id: 'more-2',
      label: own
        ? t('chatBroadcast.preview.more2Own')
        : t('chatBroadcast.preview.more2', {
            count: splitTargets(draft.settings.targets.join(' ')).length,
          }),
    },
  ];
}

function stopLabel(scene: Scene): string {
  const { t, draft } = scene;
  if (draft.settings.stop_mode === 'time') {
    return t('chatBroadcast.preview.stopTime', { hours: draft.settings.stop_hours });
  }
  const rounds = roundsOf(draft);
  if (rounds === 1)
    return t('chatBroadcast.preview.stopCountOnce', { count: draft.settings.stop_messages });
  return t('chatBroadcast.preview.stopCount', {
    rounds:
      rounds === null
        ? t('chatBroadcast.preview.endless')
        : t('chatBroadcast.preview.rounds', { count: rounds }),
    count: draft.settings.stop_messages,
  });
}

export function buildBlocks(t: TFunction, draft: Draft, ownTitles: string[]): Block[] {
  const scene: Scene = { t, draft, ownTitles };
  const own = draft.settings.target_mode === 'own';
  const blocks = own ? ownBlocks(scene) : listBlocks(scene);
  const targets = splitTargets(draft.settings.targets.join(' '));
  blocks.push({
    kind: 'divider',
    id: 'more',
    label: own
      ? t('chatBroadcast.preview.moreOwn')
      : t('chatBroadcast.preview.more', { count: targets.length }),
  });
  if (draft.settings.loop) {
    const names = own
      ? ownTitles.slice(0, 2)
      : targets.filter((value) => !value.includes('addlist')).slice(0, 2);
    blocks.push(...roundTwo(scene, names));
  }
  blocks.push({ kind: 'divider', id: 'end', label: stopLabel(scene), strong: true });
  return blocks;
}

export function warningsOf(t: TFunction, draft: Draft): { blocking: string[]; soft: string[] } {
  const blocking: string[] = [];
  const soft: string[] = [];
  if (draft.accountIds.length === 0) blocking.push(t('chatBroadcast.preview.noAccounts'));
  if (draft.settings.target_mode === 'list' && draft.settings.targets.length === 0) {
    blocking.push(t('chatBroadcast.preview.noChats'));
  }
  const ready = filledMessages(draft);
  if (ready.length === 0) blocking.push(t('chatBroadcast.preview.noMessages'));
  if (ready.length > 0 && ready.length < draft.messages.length) {
    soft.push(t('chatBroadcast.preview.emptySkipped'));
  }
  const flat = ready.some((message) => message.kind === 'text' && !VARIANTS.test(message.text));
  if (draft.settings.first_message === 'template' && !draft.settings.randomize && flat) {
    soft.push(t('chatBroadcast.preview.sameText'));
  }
  if (draft.settings.join_delay_minutes === 0 && draft.settings.target_mode === 'list') {
    soft.push(t('chatBroadcast.preview.joinAtOnce'));
  }
  return { blocking, soft };
}

// The ceiling the header shows: chats × chain × rounds, capped by the message stop.
export function upToOf(draft: Draft): number | null {
  const rounds = roundsOf(draft);
  if (draft.settings.target_mode === 'own' || rounds === null) return null;
  const total =
    splitTargets(draft.settings.targets.join(' ')).length * filledMessages(draft).length * rounds;
  return draft.settings.stop_mode === 'count'
    ? Math.min(total, draft.settings.stop_messages)
    : total;
}
