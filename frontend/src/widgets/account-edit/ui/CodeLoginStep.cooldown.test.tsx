import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { CodeLoginStep } from './CodeLoginStep';

afterEach(() => {
  vi.useRealTimers();
});

test('the resend cooldown follows the clock, so a throttled background tab catches up', async () => {
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify({ account_id: 'acc-1', phone: '+79990001122' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  );
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] });
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <CodeLoginStep accountId="acc-1" phone="+79990001122" onDone={vi.fn()} />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText('Отправить код'));
  expect(await screen.findByText(/Код отправлен/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Новый код через/ })).toBeDisabled();

  // The tab was throttled: no tick fired for 31 s, yet the next one must unlock the button.
  vi.setSystemTime(Date.now() + 31_000);
  act(() => {
    vi.advanceTimersByTime(1000);
  });
  expect(screen.getByRole('button', { name: 'Отправить новый код' })).toBeEnabled();
});
