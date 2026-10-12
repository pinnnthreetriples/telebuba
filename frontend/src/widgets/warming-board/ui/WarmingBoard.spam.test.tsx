import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { WarmingAccountState } from '@/shared/api';

import { WarmingBoard } from './WarmingBoard';

// Its own file: WarmingBoard.test.tsx is near the 700-line test cap. The card only
// renders the backend's verdict; how many silent checks earn it is the backend's call.

function account(id: string, spamCheckWarning: boolean): WarmingAccountState {
  return {
    account_id: id,
    label: id,
    state: 'sleeping',
    health: 'ok',
    cycles_completed: 3,
    spam_check_warning: spamCheckWarning,
  };
}

test('only the card the backend flags warns that @SpamBot is silent', () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <WarmingBoard
        warming={[account('acc-silent', true), account('acc-quiet', false)]}
        onStop={vi.fn()}
        onPromote={vi.fn()}
        busyIds={new Set<string>()}
      />
    </QueryClientProvider>,
  );

  const warnings = screen.getAllByText(/@SpamBot не отвечает несколько проверок подряд/);
  expect(warnings).toHaveLength(1);
  expect(warnings[0]).toHaveAttribute('role', 'status');
});
