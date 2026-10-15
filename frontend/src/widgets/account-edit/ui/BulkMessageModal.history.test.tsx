import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { BulkMessageOutcome } from '@/shared/api';

import { BulkMessageModal } from './BulkMessageModal';

function respond(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderModal() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Harness() {
    const [jobId, setJobId] = useState<string | null>(null);
    return (
      <BulkMessageModal
        jobId={jobId}
        onJobStarted={setJobId}
        onNewJob={() => {
          setJobId(null);
        }}
        onClose={() => undefined}
      />
    );
  }
  render(
    <QueryClientProvider client={queryClient}>
      <Harness />
    </QueryClientProvider>,
  );
}

function mockApi(results: BulkMessageOutcome[] = []) {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const path = new URL(request.url).pathname;
    if (path === '/api/v1/accounts' && request.method === 'GET') {
      return respond({
        items: ['a1', 'a2'].map((account_id) => ({
          account_id,
          status: 'alive',
          created_at: 'now',
          updated_at: 'now',
        })),
        next_cursor: null,
      });
    }
    if (path === '/api/v1/accounts/outreach-limits') {
      return respond({
        recipients: { no_repeat_days: 14, one_account_per_client: true, recheck_not_found: true },
        updated_at: null,
      });
    }
    if (path === '/api/v1/accounts/outreach-usage') return respond({ day: 'today', items: [] });
    if (path === '/api/v1/accounts/outreach-presets') return respond({ items: [] });
    if (path === '/api/v1/accounts/bulk-messages' && request.method === 'POST') {
      return respond({ job_id: 'job-1', status: 'running', total: 1, completed: 0, results: [] });
    }
    if (path === '/api/v1/accounts/bulk-messages/job-1') {
      return respond({
        job_id: 'job-1',
        status: 'completed',
        total: results.length,
        completed: results.length,
        results,
      });
    }
    throw new Error(`Unexpected request: ${request.method} ${path}`);
  });
}

async function startRun() {
  await userEvent.click(screen.getByRole('button', { name: 'Добавить аккаунты' }));
  const picker = await screen.findByRole('dialog', { name: 'Добавить аккаунты' });
  await waitFor(() => {
    expect(within(picker).getByRole('checkbox', { name: /Выбрать все/ })).toBeEnabled();
  });
  await userEvent.click(within(picker).getByRole('checkbox', { name: /Выбрать все/ }));
  await userEvent.click(within(picker).getByRole('button', { name: 'Добавить (2)' }));
  await userEvent.type(screen.getByLabelText('Получатели'), '@client');
  await userEvent.type(screen.getByLabelText('Сообщение'), 'Hello');
  await userEvent.click(screen.getByRole('button', { name: 'Начать отправку' }));
}

async function sentBody(): Promise<Record<string, unknown>> {
  const request = await waitFor(() => {
    const match = vi.mocked(fetch).mock.calls.find(([input]) => {
      const value = input as Request;
      return value.method === 'POST' && new URL(value.url).pathname.endsWith('/bulk-messages');
    });
    expect(match).toBeDefined();
    return match?.[0] as Request;
  });
  return (await request.clone().json()) as Record<string, unknown>;
}

test('the saved recipient rules start the form and a run sends its own', async () => {
  mockApi();
  renderModal();
  await userEvent.click(screen.getByText('Дополнительно'));

  expect(await screen.findByLabelText('Не писать повторно, дней')).toHaveValue(14);
  await userEvent.click(screen.getByRole('switch', { name: 'Не писать повторно' }));
  expect(screen.queryByLabelText('Не писать повторно, дней')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('switch', { name: 'Один клиент — один аккаунт' }));
  await startRun();

  expect((await sentBody()).recipient_rules).toEqual({
    no_repeat_days: null,
    one_account_per_client: false,
    recheck_not_found: true,
  });
});

test('recipients skipped by history are listed apart from failures, with the reason', async () => {
  mockApi([
    { account_id: 'a1', recipient: '@recent', status: 'skipped', error_code: 'recently_messaged' },
    {
      account_id: 'a2',
      recipient: '@theirs',
      status: 'skipped',
      error_code: 'owned_by_other_account',
    },
    { account_id: 'a1', recipient: '@broken', status: 'failed', error_code: 'failed' },
  ]);
  renderModal();
  await startRun();

  expect(await screen.findByText('2 получателя пропущены по истории')).toBeInTheDocument();
  expect(screen.getByText(/@recent: ему уже писали недавно/)).toBeInTheDocument();
  expect(
    screen.getByText(/@theirs: с этим клиентом уже переписывается другой аккаунт/),
  ).toBeInTheDocument();
  expect(screen.getByText('1 результат требует внимания')).toBeInTheDocument();
});
