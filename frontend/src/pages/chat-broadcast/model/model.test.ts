import { expect, test } from 'vitest';

import { i18n } from '@/shared/i18n';

import { allResting, applyOrder, historyLine, inGroup, orderOf, priority, rowView } from './board';
import {
  bodyOf,
  DEFAULT_SETTINGS,
  draftOf,
  emptyMessage,
  isFilled,
  sameDraft,
  splitTargets,
} from './draft';
import { conflictCode } from './errors';
import { board, row, SETTINGS_READ } from './fixtures.test-helpers';
import { pipelineView } from './pipeline';
import { buildBlocks, clock, resolveText, roundsOf, upToOf, warningsOf } from './preview';

const t = i18n.t.bind(i18n);
const time = (iso: string) => iso.slice(11, 16);
const ctx = { board: board(), now: Date.parse('2026-10-06T12:00:00Z'), time, approvalHours: 24 };

test('a draft round-trips the server shape and keeps defaults', () => {
  const draft = draftOf(SETTINGS_READ);

  expect(draft.settings.join_delay_minutes).toBe(DEFAULT_SETTINGS.join_delay_minutes);
  expect(draft.messages.map((m) => m.id)).toEqual([1, 2]);
  const body = bodyOf(draft, 'stamp');
  expect(body.expected_updated_at).toBe('stamp');
  expect(body.settings.messages?.[1]).toEqual({
    kind: 'post',
    text: '',
    photo: null,
    post: 't.me/mychannel/42',
  });
  expect(sameDraft(draft, draftOf(SETTINGS_READ))).toBe(true);
  expect(emptyMessage(draft.messages).id).toBe(3);
  expect(splitTargets('@a, @b\n t.me/c;')).toEqual(['@a', '@b', 't.me/c']);
});

test('which messages are filled', () => {
  const draft = draftOf(SETTINGS_READ);
  const empty = { id: 9, kind: 'text' as const, text: ' ', photo: null, post: '' };
  const badPost = { ...empty, kind: 'post' as const, post: 't.me/channel' };

  expect(isFilled(draft.messages[0]!, draft, 0)).toBe(true);
  expect(isFilled(draft.messages[1]!, draft, 1)).toBe(true);
  expect(isFilled(empty, draft, 1)).toBe(false);
  expect(isFilled(badPost, draft, 1)).toBe(false);
  const ai = {
    ...draft,
    settings: { ...draft.settings, first_message: 'ai' as const, ai_brief: 'x' },
  };
  expect(isFilled(empty, ai, 0)).toBe(true);
});

test('row views name the state, the time and the tone', () => {
  const views = ctx.board.rows.map((item) => rowView(t, item, ctx));

  expect(views[0]).toEqual({ label: 'Пишет', tone: 'success', when: 'отправлено 10:05' });
  expect(views[1]).toMatchObject({
    label: 'Ждёт до 15:40',
    tone: 'warning',
    when: 'пишет в 15:40',
  });
  expect(views[2]).toMatchObject({
    label: 'Пропущен: только админы',
    tone: 'danger',
    when: 'пропущен 10:03',
  });
  const stopped = {
    ...ctx,
    board: board({ campaign: { ...ctx.board.campaign, status: 'stopped' } }),
  };
  expect(rowView(t, row({ state: 'waiting' }), stopped).label).toBe('Пауза');
  const resting = {
    ...ctx,
    board: board({ campaign: { ...ctx.board.campaign, rest_until: '2026-10-06T16:30:00Z' } }),
  };
  expect(rowView(t, row({ state: 'round_done' }), resting)).toMatchObject({
    label: 'Отдых до 16:30',
    when: 'круг 2 в 16:30',
  });
  for (const state of [
    'pending_approval',
    'joining',
    'captcha',
    'reconnecting',
    'waiting_account',
    'queued',
  ] as const) {
    expect(rowView(t, row({ state, requested_at: '2026-10-06T10:00:00Z' }), ctx).label).not.toBe(
      '',
    );
  }
  expect(rowView(t, row({ state: 'done', last_sent_at: null }), ctx).when).toBe('готово');
  expect(rowView(t, row({ state: 'skipped', skip_reason: 'not_approved' }), ctx).label).toBe(
    'Пропущен: не приняли за 24 ч',
  );
});

test('rows are ordered by importance once, and groups split them', () => {
  const rows = [
    row({ chat_key: 'q' }),
    row({ chat_key: 'w', state: 'writing' }),
    row({ chat_key: 'c', state: 'captcha' }),
  ];
  const order = orderOf(rows);

  expect(order).toEqual(['c', 'w', 'q']);
  const later = [...rows, row({ chat_key: 'new', state: 'reconnecting' })];
  expect(applyOrder(later, order).map((item) => item.chat_key)).toEqual(['c', 'w', 'q', 'new']);
  expect(priority(row({ state: 'pending_approval' }))).toBe(3);
  expect(inGroup(row({ state: 'captcha' }), 'attention')).toBe(true);
  expect(inGroup(row({ state: 'skipped' }), 'skipped')).toBe(true);
  expect(inGroup(row({ state: 'done' }), 'ok')).toBe(true);
  expect(allResting([row({ state: 'round_done' })])).toBe(true);
  expect(allResting([])).toBe(false);
});

test('history lines read every kind', () => {
  const at = '2026-10-06T10:00:00Z';
  const line = (kind: string, extra: Record<string, unknown> = {}) =>
    historyLine(t, { at, round: 1, kind, ...extra } as never, {
      time,
      nameOf: (id) => `name-${id}`,
      approvalHours: 6,
    });

  expect(line('sent', { text: 'hi' })).toEqual({ text: 'hi', tone: 'sent' });
  expect(line('deleted', { text: 'hi' }).tone).toBe('deleted');
  expect(line('deleted').tone).toBe('skipped');
  expect(line('unconfirmed', { text: 'hi' }).text).toContain('hi');
  expect(line('failed', { detail: 'RPCError' }).text).toContain('RPCError');
  expect(line('joined', { detail: String(Date.parse(at) / 1000 + 3600) }).text).toContain('11:00');
  expect(line('joined', { detail: String(Date.parse(at) / 1000) }).text).toContain('сразу');
  expect(line('requested').text).toContain('6 ч');
  expect(line('handed', { detail: 'a1' }).text).toContain('name-a1');
  expect(line('handed').text).toBe('Чат передан вручную');
  expect(line('skipped', { detail: 'manual' }).text).toContain('вручную');
  expect(line('approved').text).toBe('Заявку приняли');
});

test('the pipeline view for a draft, a run and a stall', () => {
  const settings = draftOf(SETTINGS_READ).settings;
  const draftBoard = board({ phase: 'draft', rows: [] });
  const draft = pipelineView(t, {
    board: draftBoard,
    settings,
    missing: ['выбрать аккаунты'],
    time,
    nameOf: (id) => id,
  });
  expect(draft.action).toMatchObject({ kind: 'start', disabled: true });
  expect(draft.notice.text).toContain('выбрать аккаунты');
  expect(draft.progress).toBeNull();

  const running = pipelineView(t, {
    board: board({ resumed_at: '2026-10-06T10:02:00Z' }),
    settings,
    missing: [],
    time,
    nameOf: (id) => id,
  });
  expect(running.action).toMatchObject({ kind: 'stop', disabled: false });
  expect(running.badge.label).toBe('Идёт');
  expect(running.nodes.map((node) => node.id)).toEqual([
    'accounts',
    'chats',
    'join',
    'chain',
    'rounds',
  ]);
  expect(running.progress).toEqual({ sent: 4, total: 18 });
  expect(running.chips.map((chip) => chip.label)).toEqual(['Продолжено после перезапуска · 10:02']);

  const stalled = pipelineView(t, {
    board: board({
      phase: 'stalled',
      accounts: [
        { account_id: 'a1', state: 'halted', halted_reason: 'peer_flood', busy_owner: null },
        { account_id: 'a2', state: 'busy', halted_reason: null, busy_owner: 'warming' },
      ],
      counters: { ...board().counters, handed: 2 },
    }),
    settings,
    missing: [],
    time,
    nameOf: (id) => `n-${id}`,
  });
  expect(stalled.action).toMatchObject({ kind: 'resume', disabled: true });
  expect(stalled.extras.map((extra) => extra.tone)).toEqual(['warning', 'info']);
  expect(stalled.extras[1]!.text).toContain('n-a2 (прогрев)');
  for (const phase of ['joining', 'resting', 'stopped', 'done', 'failed', 'stopping'] as const) {
    const view = pipelineView(t, {
      board: board({ phase }),
      settings,
      missing: [],
      time,
      nameOf: (id) => id,
    });
    expect(view.notice.text).not.toBe('');
  }
});

test('the preview plays the draft with its own numbers', () => {
  const draft = draftOf(SETTINGS_READ);
  const blocks = buildBlocks(t, draft, []);

  const chats = blocks.filter((block) => block.kind === 'chat');
  expect(chats.map((block) => block.title)).toEqual(['@alpha', 't.me/+AbCdEfGh123', 'Круг 2 из 3']);
  expect(blocks.at(-1)).toMatchObject({ kind: 'divider', strong: true });
  expect(clock(90)).toBe('15:30');
  expect(resolveText('{a|b} {group_title}', 1, '@x')).toBe('b @x');
  expect(roundsOf(draft)).toBe(3);
  expect(upToOf(draft)).toBe(12);
  expect(warningsOf(t, draft)).toEqual({ blocking: [], soft: [] });

  const own = {
    ...draft,
    settings: { ...draft.settings, target_mode: 'own' as const, loop: false },
  };
  expect(buildBlocks(t, own, ['Группа']).filter((block) => block.kind === 'chat')).toHaveLength(1);
  expect(upToOf(own)).toBeNull();
  const timeStop = {
    ...draft,
    settings: { ...draft.settings, stop_mode: 'time' as const, rounds: 0 },
  };
  expect(buildBlocks(t, timeStop, []).at(-1)).toMatchObject({ label: 'Стоп: через 6 ч' });
  expect(roundsOf(timeStop)).toBeNull();
});

test('warnings block an empty campaign and warn about risky settings', () => {
  const draft = draftOf({
    ...SETTINGS_READ,
    account_ids: [],
    settings: { targets: [], messages: [{ text: ' ' }] },
  });
  expect(warningsOf(t, draft).blocking).toHaveLength(3);

  const flat = draftOf({
    ...SETTINGS_READ,
    settings: {
      targets: ['@a'],
      randomize: false,
      join_delay_minutes: 0,
      messages: [{ text: 'same text' }, { text: '' }],
    },
  });
  expect(warningsOf(t, flat).soft).toHaveLength(3);
});

test('conflict codes are read off the error envelope', () => {
  expect(conflictCode({ error: { code: 'conflict', message: 'campaign_changed' } })).toBe(
    'campaign_changed',
  );
  expect(conflictCode({ error: { code: 'invalid' } })).toBeNull();
  expect(conflictCode(null)).toBeNull();
});
