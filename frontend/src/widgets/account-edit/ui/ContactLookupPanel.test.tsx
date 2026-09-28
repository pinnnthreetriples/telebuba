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
  const appended: string[][] = [];
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ContactLookupPanel
        accountIds={accountIds}
        onAppendRecipients={(lines) => {
          appended.push(lines);
        }}
      />
    </QueryClientProvider>,
  );
  return appended;
}

function mockApi(options: { startError?: string; results?: ContactLookupOutcome[] } = {}) {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const path = new URL(request.url).pathname;
    if (path === '/api/v1/accounts/contact-lookup' && request.method === 'POST') {
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
        status: 'completed',
        total: 3,
        completed: 3,
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
  expect(appended).toEqual([['@alice', '222']]);
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
