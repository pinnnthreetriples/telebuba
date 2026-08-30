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
const GENERATE_PATH = '/api/v1/neuroshilling/campaigns/c1/generate';
const APPROVE_PATH = '/api/v1/neuroshilling/campaigns/c1/approve';

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

test('an approved campaign warns on the editing card before the approval dies', async () => {
  routeApi([CAMPAIGN], { ...SCENARIO, scenario_status: 'approved' });
  renderPage();
  await openSettings();
  await userEvent.type(await screen.findByLabelText('Тема'), '!');

  expect(screen.getByText('Сохранение снимет утверждение')).toBeInTheDocument();
});

test('saving writes the brief first and the dialogue second', async () => {
  routeApi();
  renderPage();
  await openSettings();
  await userEvent.type(await screen.findByLabelText('Тема'), '!');
  await userEvent.click(screen.getByText('Сохранить настройки'));

  await waitFor(() => {
    expect(callsTo(SCENARIO_PATH, 'PUT')).toHaveLength(1);
  });
  // The dialogue PUT always returns the campaign to `draft`, so it has to be the
  // LAST write of the pair.
  const puts = vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => request.method === 'PUT')
    .map((request) => new URL(request.url).pathname);
  expect(puts).toEqual(['/api/v1/neuroshilling/campaigns/c1', SCENARIO_PATH]);

  const brief = (await callsTo('/api/v1/neuroshilling/campaigns/c1', 'PUT')[0]!.json()) as Record<
    string,
    unknown
  >;
  expect(brief.topic).toBe('про сервис!');
  // Still a whole-form replacement: the fields other cards own are echoed back.
  expect(brief.messages_per_hour).toBe(7);
  expect(brief.targets_raw).toBe('@chat');

  const dialogue = (await callsTo(SCENARIO_PATH, 'PUT')[0]!.json()) as Record<string, unknown>;
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

test('«Отмена» in the settings really cancels: the edits do not survive into the next save', async () => {
  // Раньше закрытие лишь прятало правки — они переживали его и уезжали на сервер со
  // следующим «Сохранить настройки», то есть записывалось то, что бросили.
  routeApi();
  renderPage();
  await openSettings();
  await userEvent.type(await screen.findByLabelText('Тема'), '!');
  expect(screen.getByText('Есть несохранённые правки')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Отмена'));
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

test('approving posts the approval on its own', async () => {
  routeApi();
  renderPage();
  await openSettings();
  await openApprove();
  // Вторая «Утвердить» — та, что в подвале диалога: утверждают прочитанное.
  const confirm = () => screen.getAllByText('Утвердить').at(-1)!;
  await waitFor(() => {
    expect(confirm()).toBeEnabled();
  });

  await userEvent.click(confirm());

  await waitFor(() => {
    expect(callsTo(APPROVE_PATH, 'POST')).toHaveLength(1);
  });
  expect(callsTo(SCENARIO_PATH, 'PUT')).toHaveLength(0);
});

const START_PATH = '/api/v1/neuroshilling/campaigns/c1/start';
const STOP_PATH = '/api/v1/neuroshilling/campaigns/c1/stop';
const LOGS_PATH = '/api/v1/logs';

function routeLaunchable(over: Partial<NeuroshillingBoard> = {}): void {
  routeApi([CAMPAIGN], LAUNCHABLE_SCENARIO, { ...LAUNCHABLE_BOARD, ...over });
}

test('the setup card saves ITS slice over an echo of every other card s fields', async () => {
  routeLaunchable();
  renderPage();
  await openSettings();
  await userEvent.click(await screen.findByRole('button', { name: '+ Чат' }));
  await userEvent.type(screen.getByLabelText('+ Чат'), '@third{Enter}');
  await userEvent.click(screen.getByText('Сохранить настройки'));

  await waitFor(() => {
    expect(callsTo('/api/v1/neuroshilling/campaigns/c1', 'PUT')).toHaveLength(1);
  });
  const body = (await callsTo('/api/v1/neuroshilling/campaigns/c1', 'PUT')[0]!.json()) as Record<
    string,
    unknown
  >;
  expect(body.targets_raw).toBe('@chat\n@other\n@third');
  // The PUT is a whole-form replacement, so the scenario card's fields and the
  // stage-six columns have to ride along untouched.
  expect(body.topic).toBe('про сервис');
  expect(body.listen_minutes).toBe(45);
  expect(body.messages_per_hour).toBe(7);
});

test('start posts once and is offered only when nothing blocks it', async () => {
  routeLaunchable();
  renderPage();
  const start = await screen.findByRole('button', { name: 'Запустить' });
  await waitFor(() => {
    expect(start).toBeEnabled();
  });

  await userEvent.click(start);

  await waitFor(() => {
    expect(callsTo(START_PATH, 'POST')).toHaveLength(1);
  });
});

test('the reserve badge counts rostered reserves that are still unspent', async () => {
  routeLaunchable({
    available: [
      ...LAUNCHABLE_BOARD.available!,
      { account_id: 'a3', title: 'Виктор', assigned: true, is_reserve: true },
      // Already promoted server-side: the flag is cleared, so it is no longer pool.
      { account_id: 'a4', title: 'Галина', assigned: true, is_reserve: false, role_id: 'r1' },
      // Banned while still flagged reserve — out of the pool for a different reason.
      { account_id: 'a5', title: 'Дина', assigned: true, is_reserve: true, state: 'banned' },
      // Unrostered accounts are somebody else's business.
      { account_id: 'a6', title: 'Егор', is_reserve: true },
    ],
  });
  renderPage();
  await openSettings();

  await userEvent.click(await screen.findByRole('button', { name: 'Настроить' }));
  expect(screen.getByText('В резерве: 1')).toBeInTheDocument();
});

test('a blocked campaign never reaches the start endpoint', async () => {
  // A draft scenario: the operator reads the reason instead of collecting a 409.
  routeApi([CAMPAIGN], SCENARIO, { ...LAUNCHABLE_BOARD, campaign: CAMPAIGN });
  renderPage();

  // Twice on purpose, and in two jobs: the sidebar's checks banner lists every
  // reason, the pipeline names the first one beside the button it greys out.
  expect(await screen.findAllByText(/Сценарий не утверждён/)).toHaveLength(2);
  expect(screen.getByRole('button', { name: 'Запустить' })).toBeDisabled();
  expect(callsTo(START_PATH, 'POST')).toHaveLength(0);
});

test('a running campaign offers Stop, which posts to the stop endpoint', async () => {
  routeLaunchable({
    campaign: { ...LAUNCHABLE_BOARD.campaign, status: 'running' },
    run: { status: 'running', sent: 1, total: 2 },
  });
  renderPage();

  await userEvent.click(await screen.findByRole('button', { name: 'Остановить' }));
  await waitFor(() => {
    expect(callsTo(STOP_PATH, 'POST')).toHaveLength(1);
  });
});

test('the activity feed is read under this page s prefix and refetched by the stream', async () => {
  routeLaunchable();
  renderPage();
  await waitFor(() => {
    expect(callsTo(LOGS_PATH, 'GET').length).toBeGreaterThan(0);
  });
  expect(new URL(callsTo(LOGS_PATH, 'GET')[0]!.url).searchParams.get('event_prefix')).toBe(
    'neuroshilling',
  );
  const before = callsTo(LOGS_PATH, 'GET').length;

  emitLogFrame();

  // `listLogs` is in the invalidation set now that the page renders the panel.
  await waitFor(
    () => {
      expect(callsTo(LOGS_PATH, 'GET').length).toBeGreaterThan(before);
    },
    { timeout: 3000 },
  );
});

test('clearing the log states the real count first and only then deletes', async () => {
  routeLaunchable();
  renderPage();

  await userEvent.click(await screen.findByRole('button', { name: 'Очистить лог' }));

  // The count spans the whole retention window, not the page on screen: an
  // operator who cleared on that impression once lost a month of history.
  expect(await screen.findByText(/412/)).toBeInTheDocument();
  expect(callsTo(LOGS_PATH, 'DELETE')).toHaveLength(0);

  await userEvent.click(screen.getByText('Очистить'));
  await waitFor(() => {
    expect(callsTo(LOGS_PATH, 'DELETE')).toHaveLength(1);
  });
  // Scoped by prefix, so neurocomment's history is untouched.
  expect(new URL(callsTo(LOGS_PATH, 'DELETE')[0]!.url).searchParams.get('event_prefix')).toBe(
    'neuroshilling',
  );
});

test('what the operator typed into the setup card survives a log frame', async () => {
  routeLaunchable();
  renderPage();
  await openSettings();
  await userEvent.click(await screen.findByRole('button', { name: '+ Чат' }));
  await userEvent.type(screen.getByLabelText('+ Чат'), '@third{Enter}');
  const before = callsTo('/api/v1/neuroshilling/campaigns', 'GET').length;

  emitLogFrame();
  await waitForRefetch(before);

  // The board IS refetched and it carries the stored targets. Reseeding the form
  // from it is the bug the once-per-campaign seeding avoids.
  expect(screen.getByText('@third')).toBeInTheDocument();
});
