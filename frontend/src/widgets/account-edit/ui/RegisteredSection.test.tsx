import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';
import type { AccountRead } from '@/shared/api';

import { RegisteredSection } from './RegisteredSection';

const ACCOUNT: AccountRead = {
  account_id: 'acc-1',
  status: 'alive',
  user_id: 1_200_000_000,
  created_at: '2026-10-01T00:00:00+00:00',
  updated_at: '2026-10-01T00:00:00+00:00',
};

async function renderSection(account: AccountRead) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RegisteredSection account={account} />
    </QueryClientProvider>,
  );
  // The card opens collapsed, like every account-editor section.
  const [header] = screen.getAllByRole('button', { name: /Возраст аккаунта/ });
  if (header) await userEvent.click(header);
}

function puts(): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => new URL(request.url).pathname.endsWith('/registered-at'));
}

test('the id estimate is only a hint and the operator date is what is saved', async () => {
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify(ACCOUNT), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  );
  await renderSection(ACCOUNT);
  expect(screen.getByText(/≈ 2020/)).toBeVisible();

  fireEvent.change(screen.getByLabelText('Дата регистрации в Telegram'), {
    target: { value: '2019-05-01' },
  });
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

  await waitFor(() => {
    expect(puts()).toHaveLength(1);
  });
  const request = puts()[0];
  expect(request?.method).toBe('PUT');
  expect(await request?.json()).toEqual({ registered_at: '2019-05-01' });
});

test('a stored date can be cleared', async () => {
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify(ACCOUNT), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  );
  await renderSection({ ...ACCOUNT, user_id: null, telegram_registered_at: '2019-05-01' });
  expect(screen.getByText(/Оценки по номеру аккаунта пока нет/)).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Очистить' }));
  await waitFor(() => {
    expect(puts()).toHaveLength(1);
  });
  expect(await puts()[0]?.json()).toEqual({ registered_at: null });
});
