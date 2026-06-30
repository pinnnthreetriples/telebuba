import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { WarmingAccountState } from '@/shared/api';

import { WarmingBoard } from './WarmingBoard';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function account(id: string, state: WarmingAccountState['state']): WarmingAccountState {
  return { account_id: id, label: id, state, health: 'ok', cycles_completed: 2, trust_score: 70 };
}

const WARMING = [account('79051184490', 'active'), account('79161234567', 'sleeping')];

test('renders an in-progress card per warming account with the stage labels', () => {
  renderWithClient(<WarmingBoard warming={WARMING} onStop={vi.fn()} busyId={null} />);
  expect(screen.getByText('79051184490')).toBeInTheDocument();
  expect(screen.getByText('79161234567')).toBeInTheDocument();
  expect(screen.getAllByText('Подписка').length).toBeGreaterThan(0);
  expect(screen.getAllByText('Отчёт').length).toBeGreaterThan(0);
});

test('stops the clicked account', async () => {
  const onStop = vi.fn();
  renderWithClient(<WarmingBoard warming={WARMING} onStop={onStop} busyId={null} />);
  await userEvent.click(screen.getAllByText('Стоп')[0]!);
  await userEvent.click(screen.getByText('Остановить'));
  expect(onStop).toHaveBeenCalledWith('79051184490');
});

test('expanding a card fetches that account real activity log', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === '/api/v1/logs') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              id: 1,
              created_at: '2026-06-30T12:04:00+00:00',
              level: 'INFO',
              status: 'success',
              account_id: '79051184490',
              event: 'warming_subscribe',
            },
          ],
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });

  renderWithClient(<WarmingBoard warming={WARMING} onStop={vi.fn()} busyId={null} />);
  await userEvent.click(screen.getAllByText('Лог активности')[0]!);
  await waitFor(() => {
    expect(screen.getByText('warming_subscribe')).toBeInTheDocument();
  });
  const fetched = vi
    .mocked(fetch)
    .mock.calls.some(([input]) => (input as Request).url.includes('account_id=79051184490'));
  expect(fetched).toBe(true);
});
