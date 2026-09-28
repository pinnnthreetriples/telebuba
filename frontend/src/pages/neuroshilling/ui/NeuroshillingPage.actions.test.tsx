import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { NeuroshillingBoard } from '@/shared/api';

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

const SCENARIO_PATH = '/api/v1/neuroshilling/campaigns/c1/scenario';
const SETTINGS_PATH = '/api/v1/neuroshilling/campaigns/c1/settings';
const APPROVE_PATH = '/api/v1/neuroshilling/campaigns/c1/approve';
const ATOMIC_ACCOUNTS = [{ account_id: 'a1', role_id: null, is_reserve: false }];
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
  expect(await callsTo(APPROVE_PATH, 'POST')[0]!.json()).toEqual({ expected_updated_at: 'now' });
  expect(callsTo(SCENARIO_PATH, 'PUT')).toHaveLength(0);
});

test('approval waits for Save and preserves an unsaved pause edited in preview', async () => {
  routeApi();
  renderPage();
  await openSettings();
  await openApprove();
  const minimum = screen.getAllByLabelText('Минимальная пауза перед шагом 1').at(-1)!;
  await userEvent.clear(minimum);
  await userEvent.type(minimum, '61');
  await userEvent.tab();
  expect(
    screen.getByText('Есть несохранённые правки — в превью показан сохранённый сценарий.'),
  ).toBeInTheDocument();
  expect(screen.getAllByText('Утвердить').at(-1)).toBeDisabled();
  expect(callsTo(APPROVE_PATH, 'POST')).toHaveLength(0);
  await userEvent.click(screen.getAllByText('Отмена').at(-1)!);
  expect(screen.getByLabelText('Минимальная пауза перед шагом 1')).toHaveValue(61);
});

test('a stale approval keeps the viewed dialogue and explains the conflict', async () => {
  routeApi();
  const routed = vi.mocked(fetch).getMockImplementation();
  if (routed === undefined) throw new Error('routeApi has to run first');
  vi.mocked(fetch).mockImplementation((input, init) => {
    const request = input as Request;
    if (new URL(request.url).pathname === APPROVE_PATH && request.method === 'POST') {
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
  await openApprove();
  await userEvent.click(screen.getAllByText('Утвердить').at(-1)!);
  await waitFor(() => expect(callsTo(APPROVE_PATH, 'POST')).toHaveLength(1));
  expect(await callsTo(APPROVE_PATH, 'POST')[0]!.json()).toEqual({ expected_updated_at: 'now' });
  expect(await screen.findByRole('alert')).toHaveTextContent('обновите страницу');
  expect(screen.getByLabelText('Текст шага 1')).toHaveValue('а работает вообще?');
});

test('approval refetches the campaign version before a subsequent Save', async () => {
  routeApi();
  const routed = vi.mocked(fetch).getMockImplementation();
  if (routed === undefined) throw new Error('routeApi has to run first');
  let updatedAt = CAMPAIGN.updated_at;
  vi.mocked(fetch).mockImplementation((input, init) => {
    const request = input as Request;
    const pathname = new URL(request.url).pathname;
    if (pathname === APPROVE_PATH && request.method === 'POST') updatedAt = 'after-approval';
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
  await openApprove();
  await userEvent.click(screen.getAllByText('Утвердить').at(-1)!);
  await waitFor(() => {
    expect(callsTo(APPROVE_PATH, 'POST')).toHaveLength(1);
  });

  await userEvent.type(await screen.findByLabelText('Тема'), '!');
  const save = screen.getByText('Сохранить настройки');
  await waitFor(() => {
    expect(save).toBeEnabled();
  });
  await userEvent.click(save);
  await waitFor(() => {
    expect(callsTo(SETTINGS_PATH, 'PUT')).toHaveLength(1);
  });
  const saved = (await callsTo(SETTINGS_PATH, 'PUT')[0]!.json()) as {
    campaign: { expected_updated_at: string };
  };
  expect(saved.campaign.expected_updated_at).toBe('after-approval');
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
  expect(callsTo(SETTINGS_PATH, 'PUT')).toHaveLength(0);
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
  expect(await callsTo(START_PATH, 'POST')[0]!.json()).toEqual({ expected_updated_at: 'now' });
});

test('a stale Start sends the viewed version and shows conflict guidance', async () => {
  routeLaunchable();
  const routed = vi.mocked(fetch).getMockImplementation();
  if (routed === undefined) throw new Error('routeApi has to run first');
  let remoteChanged = false;
  vi.mocked(fetch).mockImplementation((input, init) => {
    const request = input as Request;
    const pathname = new URL(request.url).pathname;
    if (pathname.endsWith('/board') && request.method === 'GET' && remoteChanged) {
      return Promise.resolve(
        jsonResponse({
          ...LAUNCHABLE_BOARD,
          campaign: { ...LAUNCHABLE_BOARD.campaign, updated_at: 'remote-v2' },
        }),
      );
    }
    if (pathname === START_PATH && request.method === 'POST') {
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
  const start = await screen.findByRole('button', { name: 'Запустить' });
  await waitFor(() => expect(start).toBeEnabled());
  remoteChanged = true;
  const before = callsTo('/api/v1/neuroshilling/campaigns', 'GET').length;
  emitLogFrame();
  await waitForRefetch(before);
  await userEvent.click(start);
  await waitFor(() => expect(callsTo(START_PATH, 'POST')).toHaveLength(1));
  expect(await callsTo(START_PATH, 'POST')[0]!.json()).toEqual({ expected_updated_at: 'now' });
  expect(await screen.findByRole('alert')).toHaveTextContent('обновите страницу');
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
