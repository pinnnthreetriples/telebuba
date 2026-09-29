import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { BulkMessageDraft } from './BulkMessageModal';
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
    const [visible, setVisible] = useState(true);
    const [draft, setDraft] = useState<BulkMessageDraft | null>(null);
    return visible ? (
      <BulkMessageModal
        jobId={jobId}
        initialDraft={draft}
        onJobStarted={setJobId}
        onNewJob={() => {
          setJobId(null);
        }}
        onDraftSaved={setDraft}
        onClose={() => {
          setVisible(false);
        }}
      />
    ) : (
      <button
        onClick={() => {
          setVisible(true);
        }}
      >
        Reopen
      </button>
    );
  }
  render(
    <QueryClientProvider client={queryClient}>
      <Harness />
    </QueryClientProvider>,
  );
}

type Found = { phone: string; account_id: string; user_id: number };

// Every lookup started answers with the next entry of `lookups`.
function mockApi(lookups: Found[][]) {
  let started = 0;
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const path = new URL(request.url).pathname;
    if (path === '/api/v1/accounts' && request.method === 'GET') {
      return respond({
        items: ['a1', 'a2', 'a3'].map((account_id) => ({
          account_id,
          status: 'alive',
          created_at: 'now',
          updated_at: 'now',
        })),
        next_cursor: null,
      });
    }
    if (path === '/api/v1/accounts/contact-lookup/active') return respond(null);
    if (path === '/api/v1/accounts/contact-lookup' && request.method === 'POST') {
      started += 1;
      return respond({
        job_id: `lookup-${String(started)}`,
        status: 'running',
        total: 1,
        completed: 0,
        results: [],
      });
    }
    const lookup = /^\/api\/v1\/accounts\/contact-lookup\/lookup-(\d+)$/.exec(path);
    if (lookup) {
      const rows = lookups[Number(lookup[1]) - 1] ?? [];
      return respond({
        job_id: `lookup-${lookup[1] ?? ''}`,
        status: 'completed',
        total: rows.length,
        completed: rows.length,
        results: rows.map((row) => ({ ...row, status: 'found' })),
      });
    }
    if (path === '/api/v1/accounts/bulk-messages' && request.method === 'POST') {
      return respond({ job_id: 'job-1', status: 'running' });
    }
    if (path === '/api/v1/accounts/bulk-messages/job-1') {
      return respond({ job_id: 'job-1', status: 'completed', total: 1, completed: 1, results: [] });
    }
    throw new Error(`Unexpected request: ${request.method} ${path}`);
  });
}

async function pickAllAccounts() {
  await userEvent.click(screen.getByRole('button', { name: 'Добавить аккаунты' }));
  const picker = await screen.findByRole('dialog', { name: 'Добавить аккаунты' });
  await waitFor(() => {
    expect(within(picker).getByRole('checkbox', { name: /Выбрать все/ })).toBeEnabled();
  });
  await userEvent.click(within(picker).getByRole('checkbox', { name: /Выбрать все/ }));
  await userEvent.click(within(picker).getByRole('button', { name: 'Добавить (3)' }));
}

async function lookUpAndAdd(phone: string) {
  await userEvent.type(screen.getByLabelText('Номера телефонов'), phone);
  await userEvent.click(screen.getByRole('button', { name: 'Найти' }));
  await userEvent.click(await screen.findByRole('button', { name: /^Добавить 1/ }));
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

test('a closed and reopened draft keeps the distribute mode and its pins', async () => {
  mockApi([[{ phone: '+15552220000', account_id: 'a2', user_id: 222 }]]);
  renderModal();
  await pickAllAccounts();
  await userEvent.click(screen.getByRole('radio', { name: 'Распределить' }));
  await userEvent.click(screen.getByRole('button', { name: 'Найти по номерам' }));
  await lookUpAndAdd('+15552220000');
  await userEvent.click(screen.getByRole('button', { name: 'Отмена' }));
  await userEvent.click(screen.getByRole('button', { name: 'Reopen' }));
  expect(screen.getByRole('radio', { name: 'Распределить' })).toBeChecked();
  await userEvent.type(screen.getByLabelText('Сообщение'), 'Hello');
  await userEvent.click(screen.getByRole('button', { name: 'Начать отправку' }));
  const body = await sentBody();
  expect(body.mode).toBe('split');
  expect(body.recipient_accounts).toEqual({ '222': 'a2' });
});

test('a pin whose recipient line was deleted does not reach the request', async () => {
  mockApi([[{ phone: '+15552220000', account_id: 'a2', user_id: 222 }]]);
  renderModal();
  await pickAllAccounts();
  await userEvent.click(screen.getByRole('radio', { name: 'Распределить' }));
  await userEvent.click(screen.getByRole('button', { name: 'Найти по номерам' }));
  await lookUpAndAdd('+15552220000');
  await userEvent.clear(screen.getByLabelText('Получатели'));
  await userEvent.type(screen.getByLabelText('Получатели'), ' @someone ');
  await userEvent.type(screen.getByLabelText('Сообщение'), 'Hello');
  await userEvent.click(screen.getByRole('button', { name: 'Начать отправку' }));
  const body = await sentBody();
  expect(body.recipients).toEqual(['@someone']);
  expect(body.recipient_accounts).toEqual({});
});

test('a user_id found by two accounts stays pinned to whichever finder is still selected', async () => {
  mockApi([
    [{ phone: '+15552220000', account_id: 'a1', user_id: 222 }],
    [{ phone: '+15552220000', account_id: 'a2', user_id: 222 }],
  ]);
  renderModal();
  await pickAllAccounts();
  await userEvent.click(screen.getByRole('radio', { name: 'Распределить' }));
  await userEvent.click(screen.getByRole('button', { name: 'Найти по номерам' }));
  await lookUpAndAdd('+15552220000');
  await userEvent.click(screen.getByRole('button', { name: 'Новый поиск' }));
  await lookUpAndAdd('+15552220000');
  expect(screen.getByLabelText('Получатели')).toHaveValue('222');
  // a2 (the second finder) leaves; a1 can still address 222, a3 cannot.
  await userEvent.click(screen.getByRole('button', { name: /Убрать a2/ }));
  await userEvent.type(screen.getByLabelText('Сообщение'), 'Hello');
  await userEvent.click(screen.getByRole('button', { name: 'Начать отправку' }));
  const body = await sentBody();
  expect(body.account_ids).toEqual(['a1', 'a3']);
  expect(body.recipient_accounts).toEqual({ '222': 'a1' });
});
