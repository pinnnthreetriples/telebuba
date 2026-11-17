import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { SpamBudgetWarning } from './SpamBudgetWarning';

const QUIET = {
  probes_24h: 2,
  soft_cap: 10,
  hard_cap: 24,
  out_of_turn_24h: 0,
  out_of_turn_warn_at: 5,
  daily_cap_reached: false,
  out_of_turn_warned: false,
};

function answer(budget: unknown, status = 200) {
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify(budget), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  );
}

function renderWarning() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <SpamBudgetWarning />
    </QueryClientProvider>,
  );
}

test('a fleet within its caps shows nothing', async () => {
  answer(QUIET);
  renderWarning();
  await vi.waitFor(() => {
    expect(vi.mocked(fetch)).toHaveBeenCalled();
  });
  expect(screen.queryByTestId('spam-budget-warning')).toBeNull();
});

test('the daily cap reached warns with the count and the cap', async () => {
  answer({ ...QUIET, probes_24h: 11, daily_cap_reached: true });
  renderWarning();
  const warning = await screen.findByTestId('spam-budget-warning');
  expect(warning).toHaveTextContent('Много проверок @SpamBot');
  expect(warning).toHaveTextContent('За сутки по парку проверок: 11 из 10');
  expect(warning).not.toHaveTextContent('вне очереди');
});

test('too many out-of-turn checks warn on their own', async () => {
  answer({ ...QUIET, out_of_turn_24h: 6, out_of_turn_warned: true });
  renderWarning();
  const warning = await screen.findByTestId('spam-budget-warning');
  expect(warning).toHaveTextContent('Ваших проверок вне очереди за сутки: 6');
  expect(warning).not.toHaveTextContent('Плановые проверки ждут');
});

test('both reasons are listed together', async () => {
  answer({ ...QUIET, daily_cap_reached: true, out_of_turn_warned: true, out_of_turn_24h: 7 });
  renderWarning();
  const warning = await screen.findByTestId('spam-budget-warning');
  expect(warning).toHaveTextContent('Плановые проверки ждут');
  expect(warning).toHaveTextContent('вне очереди за сутки: 7');
});

test('a failed read shows no warning rather than a false one', async () => {
  answer({ error: { code: 'internal_error', message: 'x' } }, 500);
  renderWarning();
  await vi.waitFor(() => {
    expect(vi.mocked(fetch)).toHaveBeenCalled();
  });
  expect(screen.queryByTestId('spam-budget-warning')).toBeNull();
});
