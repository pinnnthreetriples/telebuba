import { describe, expect, test } from 'vitest';

import { i18n } from '@/shared/i18n';

import { defaultTab, historyText, historyTone, rowActions, splitRows, whenText } from './board';
import {
  bodyOf,
  DEFAULT_SETTINGS,
  draftOf,
  mergeTargets,
  paceOf,
  parseTarget,
  parseTargets,
  sameDraft,
  samePace,
} from './draft';
import { board, campaign, row, SETTINGS_READ } from './fixtures.test-helpers';
import { pipelineView } from './pipeline';

const t = i18n.getFixedT('ru');
const time = (iso: string) => iso.slice(11, 16);
const NOW = Date.parse('2026-10-11T10:00:00Z');

function view(status: Parameters<typeof campaign>[0], settings = DEFAULT_SETTINGS) {
  return pipelineView(t, {
    board: board({ campaign: campaign(status) }),
    settings,
    now: NOW,
    time,
  });
}

describe('draft', () => {
  test('a target is a username, a t.me link or a phone, one key per person', () => {
    expect(parseTarget('@Ivan_P')).toEqual({ key: 'u:ivan_p', shown: '@Ivan_P' });
    expect(parseTarget('https://t.me/ivan_p/')).toEqual({ key: 'u:ivan_p', shown: '@ivan_p' });
    expect(parseTarget('+7 (900) 123-45-67')).toEqual({
      key: 'p:79001234567',
      shown: '+79001234567',
    });
    for (const bad of ['t.me/+invite', 't.me/c/1/2', 'ab', '+12']) {
      expect(parseTarget(bad)).toBeNull();
    }
  });

  test('a pasted blob adds only what is new and counts the unusable', () => {
    expect(parseTargets('@ivan, olga;  t.me/+x\n@IVAN', ['@ivan'])).toEqual({
      added: ['@olga'],
      invalid: 1,
    });
    expect(mergeTargets(['@ivan'], ['@Ivan', '@petr'])).toEqual(['@ivan', '@petr']);
  });

  test('a read opens with the defaults and saves back what it read', () => {
    const draft = draftOf(SETTINGS_READ);
    expect(draft.settings.mentions_per_story).toBe(5);
    expect(draft.settings.prime_first).toBe(true);
    expect(bodyOf(draft, 'stamp').expected_updated_at).toBe('stamp');
    expect(sameDraft(draft, draftOf(SETTINGS_READ))).toBe(true);
    const edited = { ...draft, settings: { ...draft.settings, caption: 'x' } };
    expect(sameDraft(edited, draft)).toBe(false);
  });

  test('the pace is only the gear fields', () => {
    const pace = paceOf(DEFAULT_SETTINGS);
    expect(Object.keys(pace)).not.toContain('caption');
    expect(pace.flood_pause_seconds).toBe(500);
    expect(samePace(pace, { ...pace, stop_tags: 1 })).toBe(false);
  });
});

describe('pipeline', () => {
  test.each([
    ['draft', 'start', true],
    ['ready', 'start', false],
    ['running', 'pause', false],
    ['resting', 'pause', false],
    ['paused', 'resume', false],
    ['failed', 'resume', false],
    ['done', 'restart', false],
  ] as const)('a %s campaign offers %s', (status, kind, disabled) => {
    const missing = status === 'draft' ? (['no_media'] as const) : [];
    const action = view({ status, missing: [...missing] }).action;
    expect(action.kind).toBe(kind);
    expect(action.disabled).toBe(disabled);
  });

  test('the notice words every state', () => {
    expect(view({ status: 'draft', missing: ['no_accounts', 'no_media'] }).notice).toEqual({
      tone: 'warning',
      text: 'Чтобы начать: Не выбрано ни одного аккаунта, Пул медиа пуст',
    });
    expect(view({ status: 'ready' }).notice.text).toBe('Всё готово — можно начинать');
    expect(view({ status: 'resting', rest_until: '2026-10-12T09:00:00Z' }).notice.text).toBe(
      'Лимит на сегодня выбран · продолжим завтра в 09:00',
    );
    expect(view({ status: 'paused' }).notice.text).toBe('На паузе · отмечено 0 из 2');
    expect(view({ status: 'paused', last_error: 'volume' }).notice.text).toContain(
      'Остановиться через',
    );
    expect(view({ status: 'done' }).notice.tone).toBe('success');
    expect(view({ status: 'failed', last_error: 'floods' }).notice).toEqual({
      tone: 'danger',
      text: 'Остановлено: у всех аккаунтов флуд-лимит. Добавьте аккаунты или продолжите позже',
    });
    expect(view({ status: 'failed', last_error: 'no_accounts' }).notice.text).toContain(
      'не осталось рабочих аккаунтов',
    );
  });

  test('a running campaign says when the next story is due', () => {
    const next = new Date(NOW + 120_000).toISOString();
    const shown = pipelineView(t, {
      board: board({ campaign: campaign({ status: 'running' }), next_action_at: next }),
      settings: DEFAULT_SETTINGS,
      now: NOW,
      time,
    });
    expect(shown.notice.text).toBe('Идёт · следующая сторис через 2 мин');
    const priming = pipelineView(t, {
      board: board({
        campaign: campaign({ status: 'running' }),
        next_action_at: new Date(NOW + 40_000).toISOString(),
      }),
      settings: { ...DEFAULT_SETTINGS, mode: 'priming' },
      now: NOW,
      time,
    });
    expect(priming.notice.text).toBe('Идёт · следующий диалог через 40 с');
  });

  test('the stages follow the mode and the dialog switch', () => {
    const labels = (settings = DEFAULT_SETTINGS) =>
      view({ status: 'running' }, settings).steps.map((step) => step.id);
    expect(labels()).toEqual(['accounts', 'targets', 'dialog', 'story', 'done']);
    expect(labels({ ...DEFAULT_SETTINGS, prime_first: false })).toEqual([
      'accounts',
      'targets',
      'story',
      'done',
    ]);
    expect(labels({ ...DEFAULT_SETTINGS, mode: 'priming' })).toEqual([
      'accounts',
      'targets',
      'toggle',
      'done',
    ]);
    expect(view({ status: 'done' }).steps.every((step) => step.state === 'done')).toBe(true);
    expect(view({ status: 'ready' }).steps.map((step) => step.state)).toEqual([
      'done',
      'done',
      'upcoming',
      'upcoming',
      'upcoming',
    ]);
  });

  test('the tiles count tagged, left, errors and the days to go', () => {
    const stats = view({ status: 'running' }).stats;
    expect(stats.map((stat) => stat.value)).toEqual(['0 из 2', 2, 0, 80]);
    expect(stats[3]?.label).toBe('В сутки · ещё ≈1 дн.');
    expect(view({ status: 'ready' }).started).toBe(false);
  });
});

describe('board', () => {
  test('in work comes most urgent first, done newest first', () => {
    const rows = [
      row({ target_key: 'q', state: 'queued' }),
      row({ target_key: 'w', state: 'working' }),
      row({ target_key: 'f', state: 'flood' }),
      row({ target_key: 'old', state: 'tagged', active: false, when: '2026-10-11T09:00:00Z' }),
      row({ target_key: 'new', state: 'failed', active: false, when: '2026-10-11T09:30:00Z' }),
    ];
    const { active, done } = splitRows(rows);
    expect(active.map((item) => item.target_key)).toEqual(['w', 'f', 'q']);
    expect(done.map((item) => item.target_key)).toEqual(['new', 'old']);
  });

  test('a finished campaign opens on the done tab', () => {
    expect(defaultTab('done', 0, 3)).toBe('done');
    expect(defaultTab('running', 2, 3)).toBe('active');
    expect(defaultTab('paused', 0, 1)).toBe('done');
  });

  test('«Когда» words each state', () => {
    expect(whenText(t, row(), 'resting', time)).toBe('завтра');
    expect(whenText(t, row(), 'ready', time)).toBe('—');
    expect(
      whenText(t, row({ state: 'flood', when: '2026-10-11T10:30:00Z' }), 'running', time),
    ).toBe('после паузы · 10:30');
    expect(whenText(t, row({ state: 'primed' }), 'running', time)).toBe('к ближайшей сторис');
    expect(whenText(t, row({ state: 'tagged', when: '2026-10-11T10:05:00Z' }), 'done', time)).toBe(
      '10:05',
    );
  });

  test('actions depend on the state', () => {
    expect(rowActions(row())).toEqual(['now', 'skip']);
    expect(rowActions(row({ state: 'flood' }))).toEqual(['now', 'skip']);
    expect(rowActions(row({ state: 'failed' }))).toEqual(['requeue']);
    expect(rowActions(row({ state: 'working' }))).toEqual([]);
    expect(rowActions(row({ state: 'tagged' }))).toEqual([]);
  });

  test('history lines', () => {
    const at = '2026-10-11T10:00:00Z';
    expect(historyText(t, { at, event: 'tagged', detail: '5' }, true)).toBe(
      'Отмечен в сторис (всего в ней 5)',
    );
    expect(historyText(t, { at, event: 'tagged' }, false)).toContain('Автоудаление');
    expect(historyText(t, { at, event: 'failed', detail: 'privacy' }, true)).toBe(
      'Ошибка: закрыт приватностью',
    );
    expect(historyText(t, { at, event: 'flood', detail: '500' }, true)).toContain('500 с');
    expect(historyText(t, { at, event: 'expanded', detail: '12' }, true)).toContain('12');
    expect(historyTone({ at, event: 'failed' })).toBe('danger');
    expect(historyTone({ at, event: 'dialog_created' })).toBe('muted');
  });
});
