import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { ContactLookupOutcome } from '@/shared/api';

import { ContactLookupPanel } from './ContactLookupPanel';

function respond(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderPanel(accountIds: string[]) {
  const appended: [string[], Record<string, string>][] = [];
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ContactLookupPanel
        accountIds={accountIds}
        onAppendRecipients={(lines, pins) => {
          appended.push([lines, pins]);
        }}
      />
    </QueryClientProvider>,
  );
  return appended;
}

const LIMITS = {
  contact_imports_per_day: 15,
  lookup_batch_size: 3,
  lookup_min_delay_seconds: 180,
  lookup_max_delay_seconds: 480,
  silent_limit_streak: 8,
  first_messages_per_day: 25,
  bulk_min_delay_seconds: 60,
  bulk_max_delay_seconds: 180,
  updated_at: null,
};

function mockApi(
  options: {
    startError?: string;
    results?: ContactLookupOutcome[];
    active?: boolean;
    running?: boolean;
    sent?: unknown[];
  } = {},
) {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const path = new URL(request.url).pathname;
    if (path === '/api/v1/accounts/outreach-limits' && request.method === 'GET') {
      return respond(LIMITS);
    }
    if (path === '/api/v1/accounts/contact-lookup/active' && request.method === 'GET') {
      return respond(
        options.active
          ? { job_id: 'lookup-1', status: 'running', total: 3, completed: 1, results: [] }
          : null,
      );
    }
    if (path === '/api/v1/accounts/contact-lookup' && request.method === 'POST') {
      options.sent?.push(await request.clone().json());
      if (options.startError) {
        return respond({ error: { code: 'bad_request', message: options.startError } }, 400);
      }
      return respond({
        job_id: 'lookup-1',
        status: 'running',
        total: 3,
        completed: 0,
        results: [],
      });
    }
    if (path === '/api/v1/accounts/contact-lookup/lookup-1' && request.method === 'GET') {
      return respond({
        job_id: 'lookup-1',
        status: options.running ? 'running' : 'completed',
        total: 3,
        completed: options.running ? 1 : 3,
        results: options.results ?? [],
      });
    }
    throw new Error(`Unexpected request: ${request.method} ${path}`);
  });
}

test('finds phones and appends found users (username first, id fallback)', async () => {
  mockApi({
    results: [
      { phone: '+15551110000', account_id: 'a1', status: 'found', user_id: 111, username: 'alice' },
      { phone: '+15552220000', account_id: 'a1', status: 'found', user_id: 222, username: null },
      { phone: '+15559990000', account_id: 'a1', status: 'not_found' },
    ],
  });
  const appended = renderPanel(['a1']);

  await userEvent.click(screen.getByRole('button', { name: 'Найти по номерам' }));
  await userEvent.type(
    screen.getByLabelText('Номера телефонов'),
    '+15551110000{enter}+15552220000{enter}+15559990000',
  );
  await userEvent.click(screen.getByRole('button', { name: 'Найти' }));

  // Summary reflects the completed run.
  expect(await screen.findByText(/2 найдено/)).toBeInTheDocument();
  expect(screen.getByText(/1 не найден/)).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'Добавить 2 в получатели' }));
  // The id-only user is pinned to the account that found it; @alice is not.
  expect(appended).toEqual([[['@alice', '222'], { '222': 'a1' }]]);
  expect(screen.getByText('Добавлено 2 получателя')).toBeInTheDocument();
});

test('cannot search until accounts are selected', async () => {
  mockApi();
  renderPanel([]);

  await userEvent.click(screen.getByRole('button', { name: 'Найти по номерам' }));
  await userEvent.type(screen.getByLabelText('Номера телефонов'), '+15551110000');

  expect(
    screen.getByText('Сначала выберите аккаунты выше — поиск идёт с них.'),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Найти' })).toBeDisabled();
});

test('surfaces an inline error when a run is already active', async () => {
  mockApi({ startError: 'contact_lookup_run_active' });
  renderPanel(['a1']);

  await userEvent.click(screen.getByRole('button', { name: 'Найти по номерам' }));
  await userEvent.type(screen.getByLabelText('Номера телефонов'), '+15551110000');
  await userEvent.click(screen.getByRole('button', { name: 'Найти' }));

  expect(await screen.findByText('Поиск уже выполняется.')).toBeInTheDocument();
});

test('splits comma- and semicolon-separated numbers instead of fusing them', async () => {
  const sent: unknown[] = [];
  mockApi({ sent });
  renderPanel(['a1']);

  await userEvent.click(screen.getByRole('button', { name: 'Найти по номерам' }));
  await userEvent.type(
    screen.getByLabelText('Номера телефонов'),
    '+15551110000, +15552220000;+15559990000',
  );
  expect(screen.getByText('3/1000')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Найти' }));

  expect(await screen.findByText(/0 найдено/)).toBeInTheDocument();
  expect(sent).toEqual([
    expect.objectContaining({ phones: ['+15551110000', '+15552220000', '+15559990000'] }),
  ]);
});

test('picks a still-running lookup back up after the modal was reopened', async () => {
  mockApi({
    active: true,
    results: [{ phone: '+15551110000', account_id: 'a1', status: 'found', username: 'alice' }],
  });
  renderPanel(['a1']);

  await userEvent.click(screen.getByRole('button', { name: 'Найти по номерам' }));

  expect(await screen.findByText(/1 найден/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Найти' })).not.toBeInTheDocument();
});

test('warns that id-only users are reachable only from the finding account', async () => {
  mockApi({
    results: [
      { phone: '+15551110000', account_id: 'a1', status: 'found', user_id: 111, username: 'alice' },
      { phone: '+15552220000', account_id: 'a2', status: 'found', user_id: 222, username: null },
    ],
  });
  renderPanel(['a1', 'a2']);

  await userEvent.click(screen.getByRole('button', { name: 'Найти по номерам' }));
  await userEvent.type(screen.getByLabelText('Номера телефонов'), '+15551110000');
  await userEvent.click(screen.getByRole('button', { name: 'Найти' }));

  expect(await screen.findByText(/1 найденный пользователь без @username/)).toBeInTheDocument();
});

test('no id-only warning when the finding account is the only one selected', async () => {
  mockApi({
    results: [
      { phone: '+15552220000', account_id: 'a1', status: 'found', user_id: 222, username: null },
    ],
  });
  renderPanel(['a1']);

  await userEvent.click(screen.getByRole('button', { name: 'Найти по номерам' }));
  await userEvent.type(screen.getByLabelText('Номера телефонов'), '+15552220000');
  await userEvent.click(screen.getByRole('button', { name: 'Найти' }));

  expect(await screen.findByText(/1 найден/)).toBeInTheDocument();
  expect(screen.queryByText(/без @username/)).not.toBeInTheDocument();
});

test('a running search offers Stop, not a close that would orphan it', async () => {
  mockApi({ active: true, running: true });
  renderPanel(['a1']);

  await userEvent.click(screen.getByRole('button', { name: 'Найти по номерам' }));

  expect(await screen.findByRole('button', { name: 'Остановить' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Закрыть' })).not.toBeInTheDocument();
});

test('shows the saved pacing and leaves it to the server until edited', async () => {
  const sent: unknown[] = [];
  mockApi({ sent });
  renderPanel(['a1']);

  await userEvent.click(screen.getByRole('button', { name: 'Найти по номерам' }));
  await userEvent.type(screen.getByLabelText('Номера телефонов'), '+15551110000');

  expect(
    await screen.findByText(/Новых номеров на аккаунт в сутки \(UTC\): 15\./),
  ).toBeInTheDocument();
  expect(screen.getByLabelText('От, секунд')).toHaveValue(180);
  expect(screen.getByLabelText('До, секунд')).toHaveValue(480);
  expect(screen.getByLabelText('Номеров за запрос')).toHaveValue(3);
  await userEvent.click(screen.getByRole('button', { name: 'Найти' }));

  expect(await screen.findByText(/0 найдено/)).toBeInTheDocument();
  expect(sent).toEqual([{ account_ids: ['a1'], phones: ['+15551110000'] }]);
});

test('sends the pacing the operator typed for this run', async () => {
  const sent: unknown[] = [];
  mockApi({ sent });
  renderPanel(['a1']);

  await userEvent.click(screen.getByRole('button', { name: 'Найти по номерам' }));
  await userEvent.type(screen.getByLabelText('Номера телефонов'), '+15551110000');
  const max = await screen.findByLabelText('До, секунд');
  await screen.findByDisplayValue('480');
  await userEvent.clear(max);
  await userEvent.type(max, '3600');
  await userEvent.clear(screen.getByLabelText('Номеров за запрос'));
  await userEvent.type(screen.getByLabelText('Номеров за запрос'), '5');
  await userEvent.click(screen.getByRole('button', { name: 'Найти' }));

  expect(await screen.findByText(/0 найдено/)).toBeInTheDocument();
  expect(sent).toEqual([
    expect.objectContaining({ min_delay_seconds: 180, max_delay_seconds: 3600, batch_size: 5 }),
  ]);
});

test('rejects a batch size above 20', async () => {
  mockApi();
  renderPanel(['a1']);

  await userEvent.click(screen.getByRole('button', { name: 'Найти по номерам' }));
  await userEvent.type(screen.getByLabelText('Номера телефонов'), '+15551110000');
  await userEvent.clear(screen.getByLabelText('Номеров за запрос'));
  await userEvent.type(screen.getByLabelText('Номеров за запрос'), '21');

  expect(screen.getByText('Проверьте паузу и размер порции.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Найти' })).toBeDisabled();
});

test('separates daily-limit and re-check numbers and puts them back for later', async () => {
  mockApi({
    results: [
      { phone: '+15550000001', account_id: 'a1', status: 'not_found' },
      {
        phone: '+15550000002',
        account_id: 'a1',
        status: 'recheck',
        error_code: 'silent_limit',
        handed_over: true,
      },
      { phone: '+15550000002', account_id: 'a2', status: 'not_found' },
      { phone: '+15550000003', account_id: 'a1', status: 'recheck', error_code: 'silent_limit' },
      { phone: '+15550000004', account_id: 'a2', status: 'skipped', error_code: 'daily_limit' },
      { phone: '+15550000005', account_id: 'a2', status: 'skipped', error_code: 'flood_wait' },
    ],
  });
  renderPanel(['a1', 'a2']);

  await userEvent.click(screen.getByRole('button', { name: 'Найти по номерам' }));
  await userEvent.type(screen.getByLabelText('Номера телефонов'), '+15550000001');
  await userEvent.click(screen.getByRole('button', { name: 'Найти' }));

  // The handed-over row is history: +15550000002 counts once, as a2's "not found".
  expect(
    await screen.findByText(
      '0 найдено · 2 не найдено · 1 на перепроверку · 1 не проверен — дневной лимит · 1 пропущен',
    ),
  ).toBeInTheDocument();
  await userEvent.click(
    screen.getByRole('button', { name: 'Вернуть 2 непроверенных номера в поле' }),
  );

  expect(screen.getByLabelText('Номера телефонов')).toHaveValue('+15550000003\n+15550000004');
  expect(screen.getByRole('button', { name: 'Найти' })).toBeEnabled();
});
