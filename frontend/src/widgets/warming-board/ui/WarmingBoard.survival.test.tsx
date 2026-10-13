import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { WarmingAccountState } from '@/shared/api';

import { WarmingBoard } from './WarmingBoard';

// Its own file: WarmingBoard.test.tsx is near the 700-line test cap. A resumed account
// was released before, so its card keeps counting the days since that release.

function account(id: string, survival?: WarmingAccountState['survival']): WarmingAccountState {
  return {
    account_id: id,
    label: id,
    state: 'sleeping',
    health: 'ok',
    cycles_completed: 3,
    survival,
  };
}

test('only a released card wears the survival badge', () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <WarmingBoard
        warming={[
          account('acc-resumed', { days_since_release: 4, status: 'wounded', wounds: 1 }),
          account('acc-new'),
        ]}
        onStop={vi.fn()}
        onPromote={vi.fn()}
        busyIds={new Set<string>()}
      />
    </QueryClientProvider>,
  );

  expect(screen.getAllByText(/после выпуска/)).toHaveLength(1);
  expect(screen.getByText('День 4 после выпуска · жив, было ранение')).toBeInTheDocument();
});
