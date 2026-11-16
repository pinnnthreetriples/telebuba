import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { SessionGuardCard } from './SessionGuardCard';

function serve(body: unknown): void {
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } }),
    ),
  );
}

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <SessionGuardCard />
    </QueryClientProvider>,
  );
}

test('a working guard says when it last did something', async () => {
  serve({ running: true, last_activity_at: '2026-11-16T10:00:00+00:00', stalled: false });
  renderCard();

  expect(await screen.findByRole('status')).toHaveTextContent('Работает · последнее действие');
});

test('a silent guard is an alert', async () => {
  serve({
    running: true,
    last_activity_at: '2026-11-16T10:00:00+00:00',
    stalled: true,
    reason: 'silent',
  });
  renderCard();

  expect(await screen.findByRole('alert')).toHaveTextContent('Молчит с');
});

test('a guard that stopped says it is restarting', async () => {
  serve({ running: false, last_activity_at: null, stalled: true, reason: 'stopped' });
  renderCard();

  expect(await screen.findByRole('alert')).toHaveTextContent('Остановился и перезапускается');
});
