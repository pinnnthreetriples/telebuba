import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { LeadQueueItem } from '@/shared/api';

import { leadsFrom } from '../model/leads';
import { LeadQueuePanel } from './LeadQueuePanel';

function respond(body: unknown, status = 200): Response {
  return new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

type Sent = { method: string; path: string; body: unknown };

function mockApi(options: { paused?: boolean; items?: LeadQueueItem[] } = {}) {
  const sent: Sent[] = [];
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const path = new URL(request.url).pathname;
    const text = request.method === 'GET' ? '' : await request.text();
    sent.push({ method: request.method, path, body: text ? JSON.parse(text) : null });
    if (path === '/api/v1/accounts/lead-queue' && request.method === 'GET') {
      return respond({
        settings: {
          account_ids: ['saved-1'],
          text: 'Saved text',
          paused: options.paused ?? true,
          owner_user_id: 'u1',
        },
        counts: { pending: 2, sent: 5 },
        items: options.items ?? [],
        wait_reason: null,
        next_send_at: null,
      });
    }
    if (path === '/api/v1/accounts/lead-queue/leads') {
      return respond({ added: 1, duplicates: 1, invalid: 0 });
    }
    if (path === '/api/v1/accounts/lead-queue/settings') {
      return respond({ account_ids: [], text: '', paused: true, owner_user_id: 'u1' });
    }
    if (path.startsWith('/api/v1/accounts/lead-queue/leads/')) {
      return new Response(null, { status: 204 });
    }
    return respond({ error: { code: 'not_found', message: 'no' } }, 404);
  });
  return sent;
}

function renderPanel(accountIds: string[], message: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <LeadQueuePanel accountIds={accountIds} message={message} />
    </QueryClientProvider>,
  );
}

const lead = (id: number, phone: string, status: LeadQueueItem['status']): LeadQueueItem => ({
  id,
  phone,
  phone_key: phone.replace('+', ''),
  status,
  created_at: '2026-10-15T10:00:00Z',
});

test('a line splits into the number and the name only at a comma, semicolon or tab', () => {
  expect(leadsFrom('+7 999 123-45-67, Иван Петров\n\n79120000000;Анна\n+15550001111')).toEqual([
    { phone: '+7 999 123-45-67', name: 'Иван Петров' },
    { phone: '79120000000', name: 'Анна' },
    { phone: '+15550001111' },
  ]);
});

test('adding numbers sends each line with its name and reports what went in', async () => {
  const sent = mockApi();
  const user = userEvent.setup();
  renderPanel(['a1'], 'Hello');
  await user.click(screen.getByRole('button', { name: 'Очередь заявок' }));

  await user.type(screen.getByLabelText('Номера'), '+7 999 123-45-67, Иван{enter}+15550001111');
  await user.click(screen.getByRole('button', { name: 'Добавить в очередь' }));

  expect(await screen.findByText('Добавлено: 1 · уже в очереди: 1 · с ошибкой: 0')).toBeTruthy();
  expect(sent.find((call) => call.path === '/api/v1/accounts/lead-queue/leads')?.body).toEqual({
    leads: [{ phone: '+7 999 123-45-67', name: 'Иван' }, { phone: '+15550001111' }],
  });
});

test('starting the queue takes the accounts and text from the form', async () => {
  const sent = mockApi({ paused: true });
  const user = userEvent.setup();
  renderPanel(['a1', 'a2'], 'Привет');
  await user.click(screen.getByRole('button', { name: 'Очередь заявок' }));

  await user.click(
    await screen.findByRole('button', { name: 'Запустить с этими аккаунтами и текстом' }),
  );
  const saves = () => sent.filter((call) => call.path === '/api/v1/accounts/lead-queue/settings');
  await vi.waitFor(() => {
    expect(saves()).toHaveLength(1);
  });
  expect(saves()[0]?.body).toEqual({ account_ids: ['a1', 'a2'], text: 'Привет', paused: false });
});

test('a running queue can be paused and shows its counts', async () => {
  const sent = mockApi({ paused: false });
  const user = userEvent.setup();
  renderPanel(['a1'], 'Hello');
  await user.click(screen.getByRole('button', { name: 'Очередь заявок' }));

  expect(await screen.findByText('Работает · аккаунтов: 1')).toBeTruthy();
  expect(screen.getByRole('status').textContent).toContain('отправлено: 5');
  await user.click(screen.getByRole('button', { name: 'Поставить на паузу' }));

  await vi.waitFor(() => {
    expect(sent.find((call) => call.path === '/api/v1/accounts/lead-queue/settings')?.body).toEqual(
      { account_ids: ['saved-1'], text: 'Saved text', paused: true },
    );
  });
});

test('only a lead no run holds yet offers removal', async () => {
  const sent = mockApi({
    items: [lead(2, '+15550000002', 'messaging'), lead(1, '+15550000001', 'pending')],
  });
  const user = userEvent.setup();
  renderPanel([], '');
  await user.click(screen.getByRole('button', { name: 'Очередь заявок' }));

  expect(await screen.findByText('отправляется')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Убрать +15550000002 из очереди' })).toBeNull();
  expect(
    screen.getByText('Чтобы запустить очередь, выберите аккаунты и напишите текст сообщения выше.'),
  ).toBeTruthy();
  await user.click(screen.getByRole('button', { name: 'Убрать +15550000001 из очереди' }));

  await vi.waitFor(() => {
    expect(sent.some((call) => call.method === 'DELETE' && call.path.endsWith('/leads/1'))).toBe(
      true,
    );
  });
});
