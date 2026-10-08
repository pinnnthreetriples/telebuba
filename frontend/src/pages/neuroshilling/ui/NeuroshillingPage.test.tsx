import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { NeuroshillingBoard, NeuroshillingCampaign } from '@/shared/api';

import {
  BOARD,
  CAMPAIGN,
  callsTo,
  emitLogFrame,
  jsonResponse,
  LAUNCHABLE_BOARD,
  LAUNCHABLE_SCENARIO,
  openApprove,
  openSettings,
  renderPage,
  routeApi,
  SCENARIO,
  waitForRefetch,
} from './NeuroshillingPage.testHelpers';

const CAMPAIGN_PATH = '/api/v1/neuroshilling/campaigns/c1';
const SCENARIO_PATH = '/api/v1/neuroshilling/campaigns/c1/scenario';
const SETTINGS_PATH = '/api/v1/neuroshilling/campaigns/c1/settings';
const GENERATE_PATH = '/api/v1/neuroshilling/campaigns/c1/generate';
const ATOMIC_ACCOUNTS = [{ account_id: 'a1', role_id: null, is_reserve: false }];

function routeLaunchable(over: Partial<NeuroshillingBoard> = {}): void {
  routeApi([CAMPAIGN], LAUNCHABLE_SCENARIO, { ...LAUNCHABLE_BOARD, ...over });
}

// Keeps every PUT unresolved until the returned function is called, and routes
// everything else exactly as `routeApi` already did. What it buys is ORDER: two
// requests fired in parallel and two fired in sequence leave the same call counts
// behind, and only an unanswered first request tells them apart.
function holdPut(): () => void {
  const routed = vi.mocked(fetch).getMockImplementation();
  if (routed === undefined) throw new Error('routeApi has to run first');
  let release: () => void = () => undefined;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    if ((input as Request).method === 'PUT') await held;
    return routed(input, init);
  });
  return () => {
    release();
  };
}

test('a log-stream frame refetches this page s queries', async () => {
  routeApi();
  renderPage();
  await openSettings();
  await waitFor(() => {
    expect(screen.getByLabelText('Аккаунт роли 1')).toBeInTheDocument();
  });
  const before = callsTo('/api/v1/neuroshilling/campaigns', 'GET').length;

  emitLogFrame();

  await waitForRefetch(before);
});

test('a log-stream frame leaves the scenario query alone', async () => {
  routeApi();
  renderPage();
  await openSettings();
  expect(await screen.findByLabelText('Тема')).toBeInTheDocument();
  const scenarioBefore = callsTo(SCENARIO_PATH, 'GET').length;
  const before = callsTo('/api/v1/neuroshilling/campaigns', 'GET').length;

  emitLogFrame();
  await waitForRefetch(before);

  // The form behind this query is explicit-save; the stream flushes on every log
  // row, so refetching it here is what would empty the form under the operator.
  expect(callsTo(SCENARIO_PATH, 'GET')).toHaveLength(scenarioBefore);
});

test('what the operator is typing survives the refetch a log frame drives', async () => {
  routeApi();
  renderPage();
  await openSettings();
  await userEvent.type(await screen.findByLabelText('Тема'), ' и доставку');
  const before = callsTo('/api/v1/neuroshilling/campaigns', 'GET').length;

  emitLogFrame();
  await waitForRefetch(before);

  // The board IS refetched, and it carries the campaign's stored topic. Resyncing
  // the form from it is the bug this page is arranged to avoid.
  expect(screen.getByLabelText('Тема')).toHaveValue('про сервис и доставку');
});

test('the edit token and dialogue come from one settings snapshot despite mixed board cache', async () => {
  routeApi();
  const routed = vi.mocked(fetch).getMockImplementation();
  if (routed === undefined) throw new Error('routeApi has to run first');
  vi.mocked(fetch).mockImplementation((input, init) => {
    const request = input as Request;
    const pathname = new URL(request.url).pathname;
    if (pathname.endsWith('/board') && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ ...BOARD, campaign: CAMPAIGN }));
    }
    if (pathname === SETTINGS_PATH && request.method === 'GET') {
      return Promise.resolve(
        jsonResponse({
          campaign: { ...CAMPAIGN, name: 'Новое имя', topic: 'новая тема', updated_at: 'v2' },
          scenario: {
            ...SCENARIO,
            steps: [{ ...SCENARIO.steps![0]!, text: 'новый сценарий' }],
          },
          accounts: [{ account_id: 'a2', role_id: 'r1', is_reserve: false }],
        }),
      );
    }
    if (pathname === SCENARIO_PATH && request.method === 'GET') {
      return Promise.resolve(jsonResponse(SCENARIO));
    }
    return routed(input, init);
  });
  renderPage();
  await openSettings();
  expect(await screen.findByLabelText('Тема')).toHaveValue('новая тема');
  expect(screen.getByLabelText('Текст шага 1')).toHaveValue('новый сценарий');
  await userEvent.type(screen.getByLabelText('Тема'), '!');
  await userEvent.click(screen.getByText('Сохранить настройки'));
  await waitFor(() => expect(callsTo(SETTINGS_PATH, 'PUT')).toHaveLength(1));
  const body = (await callsTo(SETTINGS_PATH, 'PUT')[0]!.json()) as {
    campaign: { expected_updated_at: string; name: string; accounts: unknown[] };
  };
  expect(body.campaign.expected_updated_at).toBe('v2');
  expect(body.campaign.name).toBe('Новое имя');
  expect(body.campaign.accounts).toEqual([{ account_id: 'a2', role_id: 'r1', is_reserve: false }]);
  expect(callsTo(SCENARIO_PATH, 'GET')).toHaveLength(0);
});

test('a background board refresh cannot advance the token of an unsaved draft', async () => {
  routeApi();
  const routed = vi.mocked(fetch).getMockImplementation();
  if (routed === undefined) throw new Error('routeApi has to run first');
  let remoteChanged = false;
  vi.mocked(fetch).mockImplementation((input, init) => {
    const request = input as Request;
    const pathname = new URL(request.url).pathname;
    if (pathname.endsWith('/board') && request.method === 'GET' && remoteChanged) {
      return Promise.resolve(
        jsonResponse({
          ...BOARD,
          campaign: { ...CAMPAIGN, topic: 'чужая тема', updated_at: 'remote-v2' },
        }),
      );
    }
    if (pathname === SETTINGS_PATH && request.method === 'PUT') {
      return Promise.resolve(
        new Response(JSON.stringify({ error: { code: 'conflict', message: 'campaign_changed' } }), {
          status: 409,
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    }
    return routed(input, init);
  });
  renderPage();
  await openSettings();
  await userEvent.type(await screen.findByLabelText('Тема'), '!');
  const before = callsTo('/api/v1/neuroshilling/campaigns', 'GET').length;
  const boardBefore = callsTo('/api/v1/neuroshilling/campaigns/c1/board', 'GET').length;
  remoteChanged = true;
  emitLogFrame();
  await waitForRefetch(before);
  await waitFor(() => {
    expect(callsTo('/api/v1/neuroshilling/campaigns/c1/board', 'GET').length).toBeGreaterThan(
      boardBefore,
    );
  });
  await userEvent.click(screen.getByText('Сохранить настройки'));

  await waitFor(() => {
    expect(callsTo(SETTINGS_PATH, 'PUT')).toHaveLength(1);
  });
  const saved = (await callsTo(SETTINGS_PATH, 'PUT')[0]!.json()) as {
    campaign: { expected_updated_at: string };
  };
  expect(saved.campaign.expected_updated_at).toBe('now');
  expect(await screen.findByRole('alert')).toHaveTextContent('обновите страницу');
  expect(screen.getByLabelText('Тема')).toHaveValue('про сервис!');
});

test('an approved campaign warns on the editing card before the approval dies', async () => {
  routeApi([CAMPAIGN], { ...SCENARIO, scenario_status: 'approved' });
  renderPage();
  await openSettings();
  await userEvent.type(await screen.findByLabelText('Тема'), '!');

  expect(screen.getByText('Сохранение снимет утверждение')).toBeInTheDocument();
});

test('saving a dialogue sends the whole settings form in one request', async () => {
  routeApi();
  renderPage();
  await openSettings();
  await userEvent.type(await screen.findByLabelText('Тема'), '!');
  await userEvent.click(screen.getByText('Сохранить настройки'));

  await waitFor(() => {
    expect(callsTo(SETTINGS_PATH, 'PUT')).toHaveLength(1);
  });
  expect(callsTo(SCENARIO_PATH, 'PUT')).toHaveLength(0);
  expect(callsTo(CAMPAIGN_PATH, 'PUT')).toHaveLength(0);
  const saved = (await callsTo(SETTINGS_PATH, 'PUT')[0]!.json()) as Record<string, unknown>;
  const brief = saved.campaign as Record<string, unknown>;
  expect(brief.topic).toBe('про сервис!');
  expect(brief.expected_updated_at).toBe('now');
  // Still a whole-form replacement: the fields other cards own are echoed back.
  expect(brief.messages_per_hour).toBe(7);
  expect(brief.targets_raw).toBe('@chat');

  const dialogue = saved.scenario as Record<string, unknown>;
  expect(dialogue.roles).toEqual([{ role_id: 'r1', name: 'Скептик', description: 'сомневается' }]);
  expect(dialogue.steps).toEqual([
    {
      kind: 'message',
      role_id: 'r1',
      text: 'а работает вообще?',
      reply_to_position: null,
      target_position: null,
      emoji: null,
      delay_min_seconds: 60,
      delay_max_seconds: 180,
    },
  ]);
});

test('one settings save combines setup and scenario edits atomically', async () => {
  routeLaunchable();
  renderPage();
  await openSettings();
  await userEvent.type(await screen.findByLabelText('Тема'), '!');
  await userEvent.click(await screen.findByRole('button', { name: '+ Чат' }));
  await userEvent.type(screen.getByLabelText('+ Чат'), '@third{Enter}');
  await userEvent.click(screen.getByText('Сохранить настройки'));

  await waitFor(() => {
    expect(callsTo(SETTINGS_PATH, 'PUT')).toHaveLength(1);
  });
  expect(callsTo(CAMPAIGN_PATH, 'PUT')).toHaveLength(0);
  expect(callsTo(SCENARIO_PATH, 'PUT')).toHaveLength(0);
  const saved = (await callsTo(SETTINGS_PATH, 'PUT')[0]!.json()) as Record<string, unknown>;
  const body = saved.campaign as Record<string, unknown>;
  expect(body.topic).toBe('про сервис!');
  expect(body.targets_raw).toBe('@chat\n@other\n@third');
  expect(saved.scenario).toMatchObject({ roles: [{ role_id: 'r1' }] });
});

test('a slow settings Save blocks edits and closing until its response is adopted', async () => {
  routeApi();
  const release = holdPut();
  renderPage();
  await openSettings();
  const topic = await screen.findByLabelText('Тема');
  await userEvent.type(topic, '!');
  await userEvent.click(screen.getByText('Сохранить настройки'));
  await waitFor(() => {
    expect(callsTo(SETTINGS_PATH, 'PUT')).toHaveLength(1);
    expect(topic).toBeDisabled();
  });

  await userEvent.type(topic, 'позже');
  expect(topic).toHaveValue('про сервис!');
  expect(screen.getByText('Отмена')).toBeDisabled();

  release();
  await waitFor(() => {
    expect(topic).toBeEnabled();
  });
});

test('a stale settings Save preserves the draft and requires reload before another Save', async () => {
  routeApi();
  const routed = vi.mocked(fetch).getMockImplementation();
  if (routed === undefined) throw new Error('routeApi has to run first');
  vi.mocked(fetch).mockImplementation((input, init) => {
    const request = input as Request;
    if (new URL(request.url).pathname === SETTINGS_PATH && request.method === 'PUT') {
      return Promise.resolve(
        new Response(JSON.stringify({ error: { code: 'conflict', message: 'campaign_changed' } }), {
          status: 409,
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    }
    return routed(input, init);
  });
  renderPage();
  await openSettings();
  await userEvent.type(await screen.findByLabelText('Тема'), '!');
  await userEvent.click(screen.getByText('Сохранить настройки'));

  expect(await screen.findByRole('alert')).toHaveTextContent('обновите страницу');
  expect(screen.getByLabelText('Тема')).toHaveValue('про сервис!');
  expect(screen.getByText('Сохранить настройки')).toBeDisabled();
  await userEvent.click(screen.getByText('Сохранить настройки'));
  expect(callsTo(SETTINGS_PATH, 'PUT')).toHaveLength(1);
});

test('discarding a conflict reloads the remote settings before reopening', async () => {
  routeApi();
  const routed = vi.mocked(fetch).getMockImplementation();
  if (routed === undefined) throw new Error('routeApi has to run first');
  const remote = { ...CAMPAIGN, topic: 'другая тема', updated_at: 'remote-v2' };
  let conflict = false;
  vi.mocked(fetch).mockImplementation((input, init) => {
    const request = input as Request;
    const pathname = new URL(request.url).pathname;
    if (pathname === SETTINGS_PATH && request.method === 'PUT') {
      if (!conflict) {
        conflict = true;
        return Promise.resolve(
          new Response(
            JSON.stringify({ error: { code: 'conflict', message: 'campaign_changed' } }),
            {
              status: 409,
              headers: { 'Content-Type': 'application/json' },
            },
          ),
        );
      }
      return Promise.resolve(
        jsonResponse({ campaign: remote, scenario: SCENARIO, accounts: ATOMIC_ACCOUNTS }),
      );
    }
    if (pathname.endsWith('/board') && request.method === 'GET' && conflict) {
      return Promise.resolve(jsonResponse({ ...BOARD, campaign: remote }));
    }
    if (pathname === SETTINGS_PATH && request.method === 'GET' && conflict) {
      return Promise.resolve(
        jsonResponse({ campaign: remote, scenario: SCENARIO, accounts: ATOMIC_ACCOUNTS }),
      );
    }
    return routed(input, init);
  });
  renderPage();
  await openSettings();
  await userEvent.type(await screen.findByLabelText('Тема'), '!');
  await userEvent.click(screen.getByText('Сохранить настройки'));
  expect(await screen.findByRole('alert')).toHaveTextContent('обновите страницу');
  await userEvent.click(screen.getByText('Отмена'));
  await userEvent.click(screen.getByRole('button', { name: 'Закрыть без сохранения' }));
  await openSettings();

  await waitFor(() => {
    expect(screen.getByLabelText('Тема')).toHaveValue('другая тема');
    expect(screen.queryByRole('alert')).toBeNull();
  });
  await userEvent.type(screen.getByLabelText('Тема'), '!');
  await userEvent.click(screen.getByText('Сохранить настройки'));
  await waitFor(() => {
    expect(callsTo(SETTINGS_PATH, 'PUT')).toHaveLength(2);
  });
  const saved = (await callsTo(SETTINGS_PATH, 'PUT')[1]!.json()) as {
    campaign: { expected_updated_at: string };
  };
  expect(saved.campaign.expected_updated_at).toBe('remote-v2');
});

test('generation refetches the campaign version before the next settings Save', async () => {
  routeApi();
  const routed = vi.mocked(fetch).getMockImplementation();
  if (routed === undefined) throw new Error('routeApi has to run first');
  let updatedAt = CAMPAIGN.updated_at;
  vi.mocked(fetch).mockImplementation((input, init) => {
    const request = input as Request;
    const pathname = new URL(request.url).pathname;
    if (pathname === CAMPAIGN_PATH && request.method === 'PUT') {
      updatedAt = 'after-brief';
      return Promise.resolve(jsonResponse({ ...CAMPAIGN, updated_at: updatedAt }));
    }
    if (pathname === GENERATE_PATH && request.method === 'POST') updatedAt = 'after-generation';
    if (pathname.endsWith('/board') && request.method === 'GET') {
      return Promise.resolve(
        jsonResponse({ ...BOARD, campaign: { ...CAMPAIGN, updated_at: updatedAt } }),
      );
    }
    if (pathname === SETTINGS_PATH && request.method === 'GET') {
      return Promise.resolve(
        jsonResponse({
          campaign: { ...CAMPAIGN, updated_at: updatedAt },
          scenario: SCENARIO,
          accounts: ATOMIC_ACCOUNTS,
        }),
      );
    }
    return routed(input, init);
  });
  renderPage();
  await openSettings();
  await userEvent.click(await screen.findByText('Превью сценария'));
  await userEvent.click(screen.getByText('Перегенерировать'));
  await userEvent.click(screen.getByText('Сгенерировать'));
  await waitFor(() => {
    expect(screen.queryByText('Сгенерировать новый диалог?')).toBeNull();
    expect(callsTo(GENERATE_PATH, 'POST')).toHaveLength(1);
  });

  await userEvent.type(await screen.findByLabelText('Тема'), '!');
  await userEvent.click(screen.getByText('Сохранить настройки'));
  await waitFor(() => {
    expect(callsTo(SETTINGS_PATH, 'PUT')).toHaveLength(1);
  });
  const saved = (await callsTo(SETTINGS_PATH, 'PUT')[0]!.json()) as {
    campaign: { expected_updated_at: string };
  };
  expect(saved.campaign.expected_updated_at).toBe('after-generation');
});

test('«Отмена» in the settings really cancels: the edits do not survive into the next save', async () => {
  // Раньше закрытие лишь прятало правки — они переживали его и уезжали на сервер со
  // следующим «Сохранить настройки», то есть записывалось то, что бросили.
  routeApi();
  renderPage();
  await openSettings();
  await userEvent.type(await screen.findByLabelText('Тема'), '!');
  expect(screen.getByText('Есть несохранённые правки')).toBeInTheDocument();

  // The edits are dirty, so Cancel asks first; discarding is what this test is about.
  await userEvent.click(screen.getByText('Отмена'));
  expect(screen.getByText('Закрыть без сохранения?')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Закрыть без сохранения' }));
  await openSettings();

  expect(screen.queryByText('Есть несохранённые правки')).toBeNull();
  expect(await screen.findByLabelText('Тема')).toHaveValue('про сервис');

  await userEvent.click(screen.getByText('Сохранить настройки'));
  expect(callsTo(SCENARIO_PATH, 'PUT')).toHaveLength(0);
  expect(callsTo(CAMPAIGN_PATH, 'PUT')).toHaveLength(0);
});

test('regenerating over an existing dialogue confirms before it overwrites', async () => {
  routeApi();
  renderPage();
  await openSettings();
  await openApprove();
  await waitFor(() => {
    expect(screen.getByText('Перегенерировать')).toBeInTheDocument();
  });

  await userEvent.click(screen.getByText('Перегенерировать'));
  expect(screen.getByText('Сгенерировать новый диалог?')).toBeInTheDocument();
  // Nothing has been asked for yet — one click must not destroy the stored text.
  expect(callsTo(GENERATE_PATH, 'POST')).toHaveLength(0);

  await userEvent.click(screen.getByText('Сгенерировать'));
  await waitFor(() => {
    expect(callsTo(GENERATE_PATH, 'POST')).toHaveLength(1);
  });
  // Размер запроса берётся с экрана, а не из двух счётчиков рядом с кнопкой: в этом
  // сценарии одна роль и один шаг, и оба поднимаются до двух — меньше двух персон
  // диалога не составят.
  expect(await callsTo(GENERATE_PATH, 'POST')[0]!.json()).toEqual({
    persona_count: 2,
    step_count: 2,
  });
  // The answer replaces the form — that IS what the button means.
  await waitFor(() => {
    expect(screen.getByLabelText('Текст шага 1')).toHaveValue('придуманная реплика');
  });
});

test('generating drops the media step the operator picked for the old dialogue', async () => {
  const campaign: NeuroshillingCampaign = {
    ...CAMPAIGN,
    media_message_link: 'https://t.me/c/1/2',
    media_step_position: 1,
  };
  routeApi([campaign], SCENARIO, { ...BOARD, campaign });
  const routed = vi.mocked(fetch).getMockImplementation();
  if (routed === undefined) throw new Error('routeApi has to run first');
  let generated = false;
  vi.mocked(fetch).mockImplementation((input, init) => {
    const request = input as Request;
    const pathname = new URL(request.url).pathname;
    if (pathname === GENERATE_PATH && request.method === 'POST') generated = true;
    if (pathname === SETTINGS_PATH && request.method === 'GET' && generated) {
      return Promise.resolve(
        jsonResponse({
          campaign: { ...campaign, media_step_position: null, updated_at: 'generated' },
          scenario: SCENARIO,
          accounts: ATOMIC_ACCOUNTS,
        }),
      );
    }
    return routed(input, init);
  });
  renderPage();
  await openSettings();
  // Слот медиа живёт в своём диалоге под скрепкой: заполняют его редко, а строку под
  // темой он занимал всегда. Закрывается перед следующим шагом — иначе «Превью
  // сценария» осталось бы под ним.
  const attachment = () => userEvent.click(screen.getByRole('button', { name: 'Вложение' }));
  // «Отмена» есть и в подвале настроек, и в диалоге вложения; верхний — последний в DOM.
  const closeAttachment = () => userEvent.click(screen.getAllByText('Отмена').at(-1)!);
  await attachment();
  await waitFor(() => {
    expect(screen.getByLabelText('Шаг с медиа')).toHaveTextContent('#1');
  });
  await closeAttachment();

  await openApprove();
  await userEvent.click(screen.getByText('Перегенерировать'));
  await userEvent.click(screen.getByText('Сгенерировать'));

  // The generation cleared the slot server-side, and the form is seeded from the
  // server once per campaign — so the answer has to be adopted without it, or the
  // stale position stays on screen over a line nobody chose it for. Position 1 of
  // the generated dialogue is a message, so it is still an offered option: only the
  // adoption tells the two apart.
  await closeAttachment();
  await attachment();
  await waitFor(() => {
    expect(screen.getByLabelText('Шаг с медиа')).toHaveTextContent('Без медиа');
  });
  // Only the position went: the link names a message in another chat and is still
  // the one the operator pasted.
  expect(screen.getByLabelText('Ссылка на сообщение с медиа')).toHaveValue('https://t.me/c/1/2');
});

test('a campaign with no dialogue generates without asking, and stores the topic first', async () => {
  routeApi([CAMPAIGN], { campaign_id: 'c1', scenario_status: 'draft', roles: [], steps: [] });
  // The PUT is held open, which is the only way to tell "after" from "alongside": both
  // orders leave one call of each behind, and the model is briefed from the STORED
  // topic — fired in parallel, the ask can reach the server before the topic does.
  const release = holdPut();
  renderPage();
  await openSettings();
  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Сгенерировать через ИИ' })).toBeEnabled();
  });

  await userEvent.click(screen.getByRole('button', { name: 'Сгенерировать через ИИ' }));

  await waitFor(() => {
    expect(callsTo(CAMPAIGN_PATH, 'PUT')).toHaveLength(1);
  });
  expect(callsTo(GENERATE_PATH, 'POST')).toHaveLength(0);
  release();

  await waitFor(() => {
    expect(callsTo(GENERATE_PATH, 'POST')).toHaveLength(1);
  });
  expect(screen.queryByText('Сгенерировать новый диалог?')).not.toBeInTheDocument();
  expect(callsTo(CAMPAIGN_PATH, 'PUT')).toHaveLength(1);
});

test('generation confirms before replacing an unsaved step in an empty stored dialogue', async () => {
  routeApi([CAMPAIGN], { campaign_id: 'c1', scenario_status: 'draft', roles: [], steps: [] });
  renderPage();
  await openSettings();
  await userEvent.click(await screen.findByText('+ Реплика'));
  await userEvent.type(screen.getByLabelText('Текст шага 1'), 'мой текст');
  await userEvent.click(screen.getByRole('button', { name: 'Сгенерировать через ИИ' }));
  expect(screen.getByText('Сгенерировать новый диалог?')).toBeInTheDocument();
  expect(callsTo(GENERATE_PATH, 'POST')).toHaveLength(0);
  await userEvent.click(screen.getAllByText('Отмена').at(-1)!);
  expect(screen.getByLabelText('Текст шага 1')).toHaveValue('мой текст');
});
