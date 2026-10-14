import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';
import type { PoolRefillRequest } from '@/shared/api';

import { ChannelPoolFill } from './ChannelPoolFill';

const CONSENT = {
  status: 'needs_consent',
  pull_candidate: { account_id: 'acc-9', name: 'Мария', busy_with: 'chat_broadcast' },
};

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

async function findBodies(): Promise<unknown[]> {
  const requests = vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => new URL(request.url).pathname.endsWith('/channels/find'));
  return Promise.all(requests.map((request) => request.clone().json()));
}

function renderFill(
  answers: unknown[],
  refillRequest: PoolRefillRequest | null = null,
  onPoolChanged = vi.fn(),
) {
  const queue = [...answers];
  vi.mocked(fetch).mockImplementation(() => Promise.resolve(json(queue.shift() ?? {})));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ChannelPoolFill
        defaultLanguage="es"
        refillRequest={refillRequest}
        onPoolChanged={onPoolChanged}
      />
    </QueryClientProvider>,
  );
  return onPoolChanged;
}

test('a search sends the form and every fit is added to the list at once', async () => {
  const onPoolChanged = renderFill([{ status: 'ok', found: 7, checked: 4, added: 2 }]);
  await userEvent.click(screen.getByRole('button', { name: 'Найти каналы' }));
  await userEvent.type(screen.getByLabelText('Страна (необязательно, например MX)'), 'mx');
  await userEvent.type(screen.getByLabelText(/Темы через запятую/), 'fútbol, cine');
  await userEvent.click(screen.getByRole('button', { name: 'Искать' }));

  expect(await screen.findByText('Найдено 7, в список добавлено 2.')).toBeVisible();
  expect(await findBodies()).toEqual([
    { language: 'es', country: 'MX', topics: ['fútbol', 'cine'] },
  ]);
  expect(onPoolChanged).toHaveBeenCalled();
});

test('a busy account is only taken after the operator agrees', async () => {
  renderFill([CONSENT, { status: 'ok', found: 3, checked: 2, added: 1 }]);
  await userEvent.click(screen.getByRole('button', { name: 'Найти каналы' }));
  await userEvent.click(screen.getByRole('button', { name: 'Искать' }));

  expect(await screen.findByText(/можно взять «Мария» — сейчас он делает рассылку/)).toBeVisible();
  expect(await findBodies()).toHaveLength(1);
  await userEvent.click(screen.getByRole('button', { name: 'Выдернуть на поиск' }));

  await waitFor(async () => {
    expect(await findBodies()).toHaveLength(2);
  });
  expect((await findBodies())[1]).toEqual({
    language: 'es',
    country: null,
    topics: [],
    pull_account_id: 'acc-9',
  });
});

test('cancelling the consent takes nothing', async () => {
  renderFill([CONSENT]);
  await userEvent.click(screen.getByRole('button', { name: 'Найти каналы' }));
  await userEvent.click(screen.getByRole('button', { name: 'Искать' }));
  await userEvent.click(await screen.findByRole('button', { name: 'Отмена' }));

  expect(screen.queryByRole('button', { name: 'Выдернуть на поиск' })).toBeNull();
  expect(await findBodies()).toHaveLength(1);
});

test('the refill banner asks for an account for its language', async () => {
  renderFill([CONSENT], { language: 'pt', requested_at: '2026-10-14T10:00:00Z' });
  expect(screen.getByText('Нужен аккаунт для поиска каналов')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Выбрать аккаунт' }));

  expect(await screen.findByRole('button', { name: 'Выдернуть на поиск' })).toBeVisible();
  expect(await findBodies()).toEqual([{ language: 'pt', topics: [] }]);
});
