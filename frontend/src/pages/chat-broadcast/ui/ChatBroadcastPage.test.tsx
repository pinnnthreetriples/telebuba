import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test } from 'vitest';

import { board, CAMPAIGN, row, SETTINGS_READ } from '../model/fixtures.test-helpers';

import { callsTo, emitLogFrame, renderPage, routeApi } from './ChatBroadcastPage.testHelpers';

const BASE = '/api/v1/chat-broadcast/campaigns/c1';

test('the page shows the list, the pipeline and the board of the first campaign', async () => {
  routeApi();
  renderPage();

  expect(await screen.findByText('Конвейер')).toBeInTheDocument();
  expect(screen.getAllByText('Крипто-чаты').length).toBeGreaterThan(0);
  expect(screen.getByRole('button', { name: 'Остановить' })).toBeInTheDocument();
  expect(await screen.findByText('@alpha')).toBeInTheDocument();
  expect(screen.getByText('В работе · 2')).toBeInTheDocument();
  expect(screen.getByText('Завершённые · 1')).toBeInTheDocument();
});

test('the main button stops a running campaign', async () => {
  routeApi();
  renderPage();

  await userEvent.click(await screen.findByRole('button', { name: 'Остановить' }));

  await waitFor(() => {
    expect(callsTo(`${BASE}/stop`, 'POST')).toHaveLength(1);
  });
});

test('a stopped campaign continues on the version the page read', async () => {
  routeApi({
    board: board({ phase: 'stopped', campaign: { ...CAMPAIGN, status: 'stopped' } }),
  });
  renderPage();

  await userEvent.click(await screen.findByRole('button', { name: 'Продолжить' }));

  await waitFor(() => {
    expect(callsTo(`${BASE}/start`, 'POST')).toHaveLength(1);
  });
  const body = (await callsTo(`${BASE}/start`, 'POST')[0]!.clone().json()) as Record<
    string,
    string
  >;
  expect(body.expected_updated_at).toBe('2026-10-06T10:00:00+00:00');
});

test('the board filters by tab and group and acts on an opened chat', async () => {
  routeApi();
  renderPage();
  const user = userEvent.setup();

  await screen.findByText('@alpha');
  await user.click(screen.getByRole('button', { name: /^Пишет · 1/ }));
  expect(screen.queryByText('@beta')).toBeNull();
  await user.click(screen.getByRole('button', { name: /^Все · 2/ }));
  expect(screen.getByText('@beta')).toBeInTheDocument();

  const details = screen.getAllByRole('button', { name: 'История и действия' });
  await user.click(details[1]!);
  await user.click(await screen.findByRole('button', { name: 'Написать сейчас' }));
  await waitFor(() => {
    expect(callsTo(`${BASE}/targets/action`, 'POST')).toHaveLength(1);
  });
  const now = (await callsTo(`${BASE}/targets/action`, 'POST')[0]!.clone().json()) as Record<
    string,
    unknown
  >;
  expect(now).toEqual({ chat_key: 'beta', action: 'now', account_id: null });

  await user.click(screen.getAllByRole('button', { name: 'Пропустить чат' })[0]!);
  await waitFor(() => {
    expect(callsTo(`${BASE}/targets/action`, 'POST')).toHaveLength(2);
  });

  await user.click(screen.getByRole('radio', { name: 'Завершённые · 1' }));
  expect(await screen.findByText('@gamma')).toBeInTheDocument();
  expect(screen.getByText('Пропущен: только админы')).toBeInTheDocument();
});

test('a chat skipped for a deleted message is kept with no account in the request', async () => {
  routeApi({
    board: board({
      rows: [
        row({
          chat_key: 'delta',
          raw: '@delta',
          state: 'skipped',
          skip_reason: 'deleted',
          message_deleted: true,
          active: false,
        }),
      ],
    }),
  });
  renderPage();
  const user = userEvent.setup();

  await screen.findByText('@delta');
  await user.click(screen.getByRole('button', { name: 'История и действия' }));
  await user.click(await screen.findByRole('button', { name: 'Писать всё равно' }));
  await waitFor(() => {
    expect(callsTo(`${BASE}/targets/action`, 'POST')).toHaveLength(1);
  });
  const keep = (await callsTo(`${BASE}/targets/action`, 'POST')[0]!.clone().json()) as Record<
    string,
    unknown
  >;
  expect(keep).toEqual({ chat_key: 'delta', action: 'keep', account_id: null });
});

test('the opened chat stays open when a refetch moves the rows around it', async () => {
  const rows = [
    row({ chat_key: 'alpha', raw: '@alpha', state: 'writing' }),
    row({ chat_key: 'beta', raw: '@beta', state: 'waiting' }),
    row({ chat_key: 'delta', raw: '@delta', state: 'queued' }),
  ];
  const api = routeApi({ board: board({ rows }) });
  renderPage();
  const user = userEvent.setup();
  await screen.findByText('@delta');

  await user.click(screen.getAllByRole('button', { name: 'История и действия' })[2]!);
  expect(await screen.findByText('История чата')).toBeInTheDocument();
  // beta finishes: delta moves up one place in the "in progress" tab.
  api.board = board({
    rows: [rows[0]!, { ...rows[1]!, state: 'skipped', active: false }, rows[2]!],
  });
  emitLogFrame();

  await waitFor(() => {
    expect(screen.getByText('В работе · 2')).toBeInTheDocument();
  });
  const delta = screen.getByText('@delta').closest('tr')!;
  expect(within(delta).getByRole('button', { name: 'История и действия' })).toHaveAttribute(
    'aria-expanded',
    'true',
  );
});

test('resting chats collapse into one sentence', async () => {
  routeApi({
    board: board({
      phase: 'resting',
      campaign: { ...CAMPAIGN, rest_until: '2026-10-06T16:30:00Z' },
      rows: [row({ chat_key: 'alpha', state: 'round_done' })],
    }),
  });
  renderPage();

  expect(await screen.findByText(/Все чаты отдыхают до/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Показать чаты' }));
  expect(screen.getByText('@alpha')).toBeInTheDocument();
});

test('no campaigns yet: the empty state and creating the first one', async () => {
  routeApi({ campaigns: [] });
  renderPage();
  const user = userEvent.setup();

  expect(await screen.findByText('Рассылок пока нет')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '+ Новая рассылка' }));
  await user.type(screen.getByLabelText('Название рассылки'), 'Новая{Enter}');

  await waitFor(() => {
    expect(callsTo('/api/v1/chat-broadcast/campaigns', 'POST')).toHaveLength(1);
  });
});

test('deleting a campaign asks first', async () => {
  routeApi({ board: board({ phase: 'done', campaign: { ...CAMPAIGN, status: 'done' } }) });
  renderPage();
  const user = userEvent.setup();

  await user.click((await screen.findAllByLabelText('Удалить'))[0]!);
  await user.click(
    within(await screen.findByRole('dialog')).getByRole('button', { name: 'Удалить' }),
  );

  await waitFor(() => {
    expect(callsTo(BASE, 'DELETE')).toHaveLength(1);
  });
});

test('the pencil opens the settings dialog', async () => {
  routeApi({ settings: { ...SETTINGS_READ, status: 'running' } });
  renderPage();

  await userEvent.click((await screen.findAllByLabelText('Настройки'))[0]!);

  expect(await screen.findByText('Настройки рассылки')).toBeInTheDocument();
  expect(
    screen.getByText('Рассылка идёт — остановите её, чтобы изменить настройки.'),
  ).toBeInTheDocument();
});

test('clearing the log asks with the count first', async () => {
  routeApi();
  renderPage();
  const user = userEvent.setup();

  await user.click(await screen.findByRole('button', { name: 'Очистить лог' }));
  expect(await screen.findByText(/Удалится записей: 12/)).toBeInTheDocument();
  await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Очистить' }));

  await waitFor(() => {
    expect(callsTo('/api/v1/logs', 'DELETE')).toHaveLength(1);
  });
});

test('the board tabs sit beside its title and the gear saves the pauses', async () => {
  routeApi();
  renderPage();
  const user = userEvent.setup();

  const tabs = await screen.findByRole('radiogroup', { name: 'Какие чаты показать' });
  // Beside the title, not inside the toggle that folds the card.
  expect(tabs.closest('button')).toBeNull();
  await user.click(screen.getByRole('button', { name: 'Паузы рассылки' }));
  const dialog = await screen.findByRole('dialog', { name: 'Паузы рассылки' });
  const rest = within(dialog).getByLabelText('Отдых между кругами: от');
  await user.clear(rest);
  await user.type(rest, '30');
  await user.click(within(dialog).getByRole('button', { name: 'Сохранить' }));

  await waitFor(() => {
    expect(callsTo(`${BASE}/pace`, 'PUT')).toHaveLength(1);
  });
  const body = (await callsTo(`${BASE}/pace`, 'PUT')[0]!.clone().json()) as Record<
    string,
    { min: number; max: number }
  >;
  expect(body.rest_minutes?.min).toBe(30);
  expect(Object.keys(body).sort()).toEqual(['between_chats', 'between_messages', 'rest_minutes']);
  await waitFor(() => {
    expect(screen.queryByRole('dialog', { name: 'Паузы рассылки' })).toBeNull();
  });
});

test('a pause save that lost the race says so, and a retry goes through', async () => {
  const api = routeApi({ refuse: { path: '/pace', status: 409, code: 'campaign_changed' } });
  renderPage();
  const user = userEvent.setup();

  await user.click(await screen.findByRole('button', { name: 'Паузы рассылки' }));
  const dialog = await screen.findByRole('dialog', { name: 'Паузы рассылки' });
  await user.click(within(dialog).getByRole('button', { name: 'Сохранить' }));

  expect(await within(dialog).findByRole('alert')).toHaveTextContent('Рассылку изменили');
  expect(within(dialog).getByRole('button', { name: 'Сохранить' })).toBeEnabled();

  // The server re-reads on every call, so the same save now succeeds and the dialog closes.
  api.refuse = null;
  await user.click(within(dialog).getByRole('button', { name: 'Сохранить' }));
  await waitFor(() => {
    expect(screen.queryByRole('dialog', { name: 'Паузы рассылки' })).toBeNull();
  });
  expect(callsTo(`${BASE}/pace`, 'PUT')).toHaveLength(2);
});

test('the pause dialog asks before a changed range is thrown away', async () => {
  routeApi();
  renderPage();
  const user = userEvent.setup();

  await user.click(await screen.findByRole('button', { name: 'Паузы рассылки' }));
  const dialog = await screen.findByRole('dialog', { name: 'Паузы рассылки' });
  const rest = within(dialog).getByLabelText('Отдых между кругами: от');
  await user.clear(rest);
  await user.type(rest, '30');

  await user.keyboard('{Escape}');
  const question = screen.getByRole('dialog', { name: 'Закрыть без сохранения?' });
  await user.click(within(question).getByRole('button', { name: 'Закрыть без сохранения' }));

  expect(screen.queryByRole('dialog', { name: 'Паузы рассылки' })).toBeNull();
  expect(callsTo(`${BASE}/pace`, 'PUT')).toHaveLength(0);
});
