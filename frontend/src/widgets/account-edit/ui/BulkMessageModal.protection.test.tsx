import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { SkippedAccount } from '@/shared/api';

import { BulkMessageModal } from './BulkMessageModal';

function respond(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderModal(jobId: string | null = null) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <BulkMessageModal jobId={jobId} onJobStarted={vi.fn()} onNewJob={vi.fn()} onClose={vi.fn()} />
    </QueryClientProvider>,
  );
}

function mockApi(
  options: {
    young?: string[];
    skippedNow?: SkippedAccount[];
    skippedInRun?: SkippedAccount[];
    usedToday?: Record<string, number>;
  } = {},
) {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const path = new URL(request.url).pathname;
    if (path === '/api/v1/accounts' && request.method === 'GET') {
      return respond({
        items: ['a1', 'a2'].map((account_id) => ({
          account_id,
          first_name: account_id === 'a1' ? 'Анна' : 'Иван',
          status: 'alive',
          created_at: 'now',
          updated_at: 'now',
        })),
        next_cursor: null,
      });
    }
    if (path === '/api/v1/accounts/outreach-limits') {
      return respond({ first_messages_per_day: 15, updated_at: null });
    }
    if (path === '/api/v1/accounts/outreach-usage') {
      const ids = new URL(request.url).searchParams.getAll('account_ids');
      return respond({
        day: '2026-10-13',
        items: ids.map((id) => ({
          account_id: id,
          day: '2026-10-13',
          contact_imports: 0,
          contact_matches: 0,
          first_messages: options.usedToday?.[id] ?? 0,
        })),
        young_account_ids: options.young ?? [],
        skipped_accounts: options.skippedNow ?? [],
      });
    }
    if (path === '/api/v1/accounts/outreach-presets') return respond({ items: [] });
    if (path === '/api/v1/accounts/contact-lookup/active') return respond(null);
    if (path === '/api/v1/accounts/bulk-messages' && request.method === 'POST') {
      return respond({ job_id: 'job-1', status: 'running' });
    }
    if (path === '/api/v1/accounts/bulk-messages/job-1') {
      return respond({
        job_id: 'job-1',
        status: 'completed',
        total: 1,
        completed: 1,
        results: [],
        skipped_accounts: options.skippedInRun ?? [],
      });
    }
    throw new Error(`Unexpected request: ${request.method} ${path}`);
  });
}

async function pickBothAccounts() {
  await userEvent.click(screen.getByRole('button', { name: 'Добавить аккаунты' }));
  const picker = await screen.findByRole('dialog', { name: 'Добавить аккаунты' });
  await waitFor(() => {
    expect(within(picker).getByRole('checkbox', { name: /Выбрать все/ })).toBeEnabled();
  });
  await userEvent.click(within(picker).getByRole('checkbox', { name: /Выбрать все/ }));
  await userEvent.click(within(picker).getByRole('button', { name: 'Добавить (2)' }));
}

async function sentBody(): Promise<Record<string, unknown>> {
  const request = await waitFor(() => {
    const match = vi.mocked(fetch).mock.calls.find(([input]) => {
      const value = input as Request;
      return (
        value.method === 'POST' && new URL(value.url).pathname === '/api/v1/accounts/bulk-messages'
      );
    });
    expect(match).toBeDefined();
    return match?.[0] as Request;
  });
  return (await request.clone().json()) as Record<string, unknown>;
}

test('a young account shows half its cap and a left-out one says why', async () => {
  mockApi({
    young: ['a1'],
    usedToday: { a1: 4 },
    skippedNow: [{ account_id: 'a2', reason: 'account_spam_blocked' }],
  });
  renderModal();
  await pickBothAccounts();

  expect(await screen.findByText('Анна · 3 из 7 (молодой)')).toBeInTheDocument();
  expect(screen.getByText('Иван · пропустим: у аккаунта спамблок')).toBeInTheDocument();
});

test('switching protection off changes the chips and is sent with the run', async () => {
  mockApi({ young: ['a1'], skippedNow: [{ account_id: 'a2', reason: 'account_recent_flood' }] });
  renderModal();
  await pickBothAccounts();
  await userEvent.type(screen.getByLabelText('Получатели'), '@first');
  await userEvent.type(screen.getByLabelText('Сообщение'), 'Hello');
  await screen.findByText('Анна · 7 из 7 (молодой)');

  await userEvent.click(screen.getByText('Дополнительно'));
  await userEvent.click(screen.getByRole('switch', { name: /половина лимитов/ }));
  await userEvent.click(screen.getByRole('switch', { name: /Пропускать аккаунты/ }));
  await userEvent.click(screen.getByRole('switch', { name: /печатает/ }));
  await userEvent.clear(screen.getByLabelText('Отдых после лимита, часов'));
  await userEvent.type(screen.getByLabelText('Отдых после лимита, часов'), '24');

  expect(screen.getByText('Анна · 15 из 15')).toBeInTheDocument();
  expect(screen.getByText('Иван · 15 из 15')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Начать отправку' }));
  expect((await sentBody()).protection).toEqual({
    young_half_caps: false,
    young_account_days: 30,
    rest_after_limit: true,
    rest_hours: 24,
    stop_run_on_limits: true,
    stop_run_after_accounts: 2,
    skip_injured: false,
    typing: false,
    typing_min_seconds: 2,
    typing_max_seconds: 5,
  });
});

test('a finished run lists the accounts protection left out and their rest', async () => {
  mockApi({
    skippedInRun: [{ account_id: 'a1', reason: 'account_resting', retry_after_seconds: 5400 }],
  });
  renderModal('job-1');

  expect(await screen.findByText('Защита пропустила 1 аккаунт')).toBeInTheDocument();
  expect(screen.getByText(/Анна: аккаунт отдыхает после лимита/).textContent).toBe(
    'Анна: аккаунт отдыхает после лимита ещё ~2 ч',
  );
});
