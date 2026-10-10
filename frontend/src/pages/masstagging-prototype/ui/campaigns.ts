// PROTOTYPE — throwaway. Variant D's mock world: one campaign per lifecycle state, so
// clicking through the sidebar shows every screen the real page has to draw. Only the
// first campaign is wired to the shared draft; the rest carry their own mode and list.
import type { LogEntry } from '@/shared/api';
import type { BadgeTone, StepperStep } from '@/shared/ui';

import { blockers, type Draft, estimate, type Mode } from './model';

export type Status = 'draft' | 'ready' | 'running' | 'paused' | 'resting' | 'done' | 'failed';

export type MockCampaign = {
  id: string;
  name: string;
  mode: Mode;
  // The live campaign's draft/ready is computed from its settings; this is the rest.
  status: Status;
  size: number;
};

export const LIVE_ID = 'c1';

export const CAMPAIGNS: MockCampaign[] = [
  { id: 'c1', name: 'Обучение — март', mode: 'stories', status: 'draft', size: 0 },
  { id: 'c2', name: 'Прайминг ретейл', mode: 'priming', status: 'paused', size: 24 },
  { id: 'c3', name: 'Ночная волна', mode: 'stories', status: 'resting', size: 30 },
  { id: 'c4', name: 'Холодная база', mode: 'priming', status: 'failed', size: 18 },
  { id: 'c5', name: 'Тест на 20', mode: 'stories', status: 'done', size: 20 },
];

export const STATUS: Record<Status, { label: string; tone: BadgeTone }> = {
  draft: { label: 'Черновик', tone: 'neutral' },
  ready: { label: 'Готова', tone: 'neutral' },
  running: { label: 'Идёт', tone: 'info' },
  paused: { label: 'Пауза', tone: 'warning' },
  resting: { label: 'До завтра', tone: 'info' },
  done: { label: 'Готово', tone: 'success' },
  failed: { label: 'Остановлена', tone: 'danger' },
};

export const MODE_SHORT: Record<Mode, string> = { stories: 'Сторис', priming: 'Прайминг' };

export function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  const word =
    mod10 === 1 && mod100 !== 11
      ? one
      : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)
        ? few
        : many;
  return `${String(n)} ${word}`;
}

export const HOW_STEPS = [
  'Создайте кампанию и откройте настройки.',
  'Выберите режим, аккаунты и кого отмечать.',
  'Нажмите «Начать» — аккаунты пойдут по списку сами.',
  'Ход видно на доске «Цели» и в журнале.',
];

const HANDLES = [
  '@kate_morn', '@d.voronin', '@ritashop', '@alex_kz', '@nikolay77', '@mila_nails', '@vova_fit',
  '@lena.k', '@timur_b', '@ann_fox', '@max_pro', '@olga_s', '@sergey_v', '@dasha_m', '@ilya.t',
  '@nastya_w', '@roma_r', '@yulia_n', '@artur99', '@sasha_g', '@vika_l', '@egor_k', '@polina.d',
  '@kirill_z', '@marina_s', '@denis_o', '@alina_p', '@fedor_m', '@zhenya_t', '@gleb_v',
];

export function targetsOf(campaign: MockCampaign, draft: Draft): string[] {
  return campaign.id === LIVE_ID ? draft.targets : HANDLES.slice(0, campaign.size);
}

// ── Board ────────────────────────────────────────────────────────────────────

export type RowState = 'queued' | 'primed' | 'working' | 'tagged' | 'skipped' | 'failed' | 'flood';

export type BoardRow = {
  target: string;
  state: RowState;
  when: string;
  accountId: string | null;
  // Why a failed or skipped row ended there, in a few words.
  reason?: string;
  history: { at: string; text: string; tone: 'muted' | 'info' | 'danger' | 'warning' }[];
};

export const ROW: Record<RowState, { label: string; tone: BadgeTone }> = {
  queued: { label: 'В очереди', tone: 'neutral' },
  primed: { label: 'Диалог создан', tone: 'info' },
  working: { label: 'Сейчас', tone: 'info' },
  tagged: { label: 'Отмечен', tone: 'success' },
  skipped: { label: 'Пропущен', tone: 'neutral' },
  failed: { label: 'Ошибка', tone: 'danger' },
  flood: { label: 'Ждёт аккаунт', tone: 'warning' },
};

export const ACTIVE_STATES: readonly RowState[] = ['working', 'primed', 'flood', 'queued'];

const RUN_CYCLE: RowState[] = ['tagged', 'tagged', 'working', 'primed', 'tagged', 'failed', 'flood', 'primed', 'skipped', 'queued'];

function stateFor(status: Status, index: number, total: number): RowState {
  const raw = RUN_CYCLE[index % RUN_CYCLE.length] ?? 'queued';
  switch (status) {
    case 'draft':
    case 'ready':
      return 'queued';
    case 'running':
      return raw;
    case 'paused':
      return raw === 'working' || raw === 'flood' ? 'queued' : raw;
    case 'resting':
      return index < Math.ceil(total * 0.6) ? (raw === 'failed' || raw === 'skipped' ? raw : 'tagged') : 'queued';
    case 'done':
      return raw === 'failed' || raw === 'skipped' ? raw : 'tagged';
    case 'failed':
      return index < Math.ceil(total * 0.3) ? 'tagged' : index % 3 === 0 ? 'flood' : 'queued';
  }
}

export function boardRows(campaign: MockCampaign, status: Status, draft: Draft): BoardRow[] {
  const ids = draft.accountIds;
  const story = campaign.mode === 'stories';
  const dialogFirst = story && draft.primeFirst;
  const targets = targetsOf(campaign, draft);
  return targets.map((target, index) => {
    let state = stateFor(status, index, targets.length);
    if (state === 'primed' && !dialogFirst) state = 'queued';
    const accountId = state === 'queued' ? null : (ids[index % Math.max(ids.length, 1)] ?? null);
    const at = `14:${String(10 + ((index * 3) % 50)).padStart(2, '0')}`;
    const history: BoardRow['history'] = [];
    let reason: string | undefined;
    if (dialogFirst && (state === 'tagged' || state === 'primed' || state === 'working'))
      history.push({ at, tone: 'muted', text: 'Диалог создан (автоудаление вкл → выкл)' });
    if (state === 'primed') history.push({ at, tone: 'info', text: 'Ждёт ближайшую сторис' });
    if (state === 'working') history.push({ at, tone: 'info', text: story ? 'Сторис загружается' : 'Открываю личку' });
    if (state === 'tagged')
      history.push({
        at,
        tone: 'muted',
        text: story
          ? `Отмечен в сторис (ещё ${String(draft.mentionsPerStory - 1)} в ней же)`
          : 'Автоудаление вкл → выкл, строка в личке осталась',
      });
    if (state === 'failed') {
      reason = 'закрыт приватностью';
      history.push({ at, tone: 'danger', text: 'Отмечать и писать ему могут только его контакты' });
    }
    if (state === 'flood') history.push({ at, tone: 'warning', text: 'Аккаунт поймал флуд-лимит — цель ждёт' });
    if (state === 'skipped') {
      reason = 'уже отмечен';
      history.push({ at, tone: 'muted', text: 'Его уже отметила другая кампания' });
    }
    const when =
      state === 'queued'
        ? status === 'resting'
          ? 'завтра'
          : status === 'running'
            ? `через ${String(2 + index)} мин`
            : '—'
        : state === 'flood'
          ? 'после паузы'
          : state === 'primed'
            ? 'сторис ~14:52'
            : at;
    return { target, state, when, accountId, reason, history };
  });
}

// ── Pipeline ─────────────────────────────────────────────────────────────────

export type Action = { kind: 'start' | 'pause' | 'resume' | 'restart'; label: string; disabled: boolean };

export function pipeline(campaign: MockCampaign, status: Status, draft: Draft, rows: BoardRow[]) {
  const story = campaign.mode === 'stories';
  const dialogFirst = story && draft.primeFirst;
  const missing = campaign.id === LIVE_ID ? blockers(draft) : [];
  const { perDay, accounts, excluded } = estimate({ ...draft, mode: campaign.mode });
  const tagged = rows.filter((row) => row.state === 'tagged').length;
  const failed = rows.filter((row) => row.state === 'failed').length;
  const left = rows.filter((row) => ACTIVE_STATES.includes(row.state)).length;
  const days = perDay === 0 || left === 0 ? null : Math.ceil(left / perDay);

  const labels = story
    ? ['Аккаунты', 'Цели', ...(dialogFirst ? ['Диалог'] : []), 'Сторис', 'Готово']
    : ['Аккаунты', 'Цели', 'Автоудаление', 'Готово'];
  const captions: Record<string, string> = {
    Аккаунты: excluded > 0 ? `${String(accounts.length)} · ${String(excluded)} искл.` : String(accounts.length),
    Цели: String(rows.length),
    Диалог: 'автоудаление',
    Сторис: `по ${String(draft.mentionsPerStory)} в каждой`,
    Автоудаление: `${String(draft.dialogsPerDay)} в сутки`,
  };
  const hasAccounts = draft.accountIds.length > 0;
  const hasTargets = rows.length > 0;
  const workIndex = 2;
  const steps: StepperStep[] = labels.map((label, index) => {
    let state: StepperStep['state'] = 'upcoming';
    if (status === 'done') state = 'done';
    else if (status === 'draft' || status === 'ready') {
      if ((index === 0 && hasAccounts) || (index === 1 && hasTargets)) state = 'done';
    } else if (index < workIndex) state = 'done';
    else if (index === workIndex || (status === 'running' && index < labels.length - 1)) state = 'current';
    return { id: label, label, caption: captions[label], state };
  });

  const notices: Record<Status, { tone: 'info' | 'success' | 'warning' | 'danger'; text: string }> = {
    draft: { tone: 'warning', text: `Чтобы начать: ${missing.join(', ').toLowerCase()}` },
    ready: { tone: 'success', text: 'Всё готово — можно начинать' },
    running: {
      tone: 'info',
      text: story ? 'Идёт · следующая сторис через 2 мин' : 'Идёт · следующий диалог через 40 с',
    },
    paused: { tone: 'warning', text: `На паузе · отмечено ${String(tagged)} из ${String(rows.length)}` },
    resting: { tone: 'info', text: 'Лимит на сегодня выбран · продолжим завтра в 09:00' },
    done: { tone: 'success', text: `Готово · отмечено ${String(tagged)} из ${String(rows.length)}` },
    failed: { tone: 'danger', text: 'Остановлено: у всех аккаунтов флуд-лимит. Добавьте аккаунты или продолжите позже' },
  };

  const action: Action =
    status === 'running' || status === 'resting'
      ? { kind: 'pause', label: 'Пауза', disabled: false }
      : status === 'paused' || status === 'failed'
        ? { kind: 'resume', label: 'Продолжить', disabled: false }
        : status === 'done'
          ? { kind: 'restart', label: 'Запустить снова', disabled: false }
          : { kind: 'start', label: 'Начать', disabled: status === 'draft' };

  const stats = [
    { label: 'Отмечено', value: `${String(tagged)} из ${String(rows.length)}`, tone: 'success' as const },
    { label: 'Осталось', value: left, tone: 'primary' as const },
    { label: 'Ошибок', value: failed, tone: failed > 0 ? ('danger' as const) : ('default' as const) },
    { label: days === null ? 'В сутки' : `В сутки · ещё ≈${String(days)} дн.`, value: perDay },
  ];

  return {
    steps,
    notice: notices[status],
    stats,
    action,
    started: status !== 'draft' && status !== 'ready',
    progress: {
      done: rows.filter((row) => !ACTIVE_STATES.includes(row.state)).length,
      total: rows.length,
    },
  };
}

// ── Log ──────────────────────────────────────────────────────────────────────

// `event` carries Russian text on purpose: LogTerminal prints an unknown code as is,
// and the real codes (`masstagging_*`) get their labels with the backend.
export function logLines(mode: Mode, status: Status, accountIds: string[]): LogEntry[] {
  if (status === 'draft' || status === 'ready') return [];
  const story = mode === 'stories';
  const rows: [string, LogEntry['status'], string][] = story
    ? [
        ['14:30:40', 'success', '@kate_morn — диалог создан'],
        ['14:31:02', 'success', 'Сторис опубликована · отмечены @kate_morn, @d.voronin'],
        ['14:35:11', 'error', '@ritashop — закрыт приватностью, пропускаем'],
        ['14:36:02', 'warning', 'Флуд-лимит — аккаунт ждёт 500 с'],
        ['14:39:28', 'success', 'Сторис опубликована · отмечены @alex_kz, @nikolay77'],
      ]
    : [
        ['14:31:02', 'success', '@kate_morn — автоудаление вкл → выкл'],
        ['14:32:15', 'success', '@d.voronin — автоудаление вкл → выкл'],
        ['14:35:11', 'error', '@ritashop — закрыт приватностью, пропускаем'],
        ['14:36:02', 'warning', 'Флуд-лимит — аккаунт ждёт 500 с'],
        ['14:39:28', 'success', '@alex_kz — автоудаление вкл → выкл'],
      ];
  if (status === 'resting') rows.push(['18:02:00', 'warning', 'Лимит на сегодня выбран — продолжим завтра в 09:00']);
  if (status === 'failed') rows.push(['14:41:10', 'error', 'Все аккаунты на флуд-паузе — кампания остановлена']);
  if (status === 'paused') rows.push(['14:40:00', 'warning', 'Пауза по кнопке']);
  if (status === 'done') rows.push(['15:12:44', 'success', 'Список закончился — кампания завершена']);
  return rows
    .map(([time, level, event], index) => ({
      id: index + 1,
      created_at: `2026-10-11T${time}`,
      level: level === 'error' ? ('ERROR' as const) : level === 'warning' ? ('WARNING' as const) : ('INFO' as const),
      status: level,
      account_id: accountIds[index % Math.max(accountIds.length, 1)] ?? null,
      event,
    }))
    .reverse();
}
