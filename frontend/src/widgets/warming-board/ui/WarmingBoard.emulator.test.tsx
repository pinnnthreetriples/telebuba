import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { WarmingAccountState } from '@/shared/api';

import { WarmingBoard } from './WarmingBoard';

// The card only renders the backend's verdict: a phone stint whose visits are
// skipped because the emulator is off.

function account(id: string, emulatorOff: boolean): WarmingAccountState {
  return {
    account_id: id,
    label: id,
    state: 'sleeping',
    health: 'ok',
    cycles_completed: 3,
    device_mode: 'mobile',
    mobile_emulator_off: emulatorOff,
  };
}

test('only the card the backend flags warns that the emulator is off', () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <WarmingBoard
        warming={[account('acc-off', true), account('acc-on', false)]}
        onStop={vi.fn()}
        onPromote={vi.fn()}
        busyIds={new Set<string>()}
      />
    </QueryClientProvider>,
  );

  const warnings = screen.getAllByText(/Эмулятор выключен: визиты с телефона пропускаются/);
  expect(warnings).toHaveLength(1);
  expect(warnings[0]).toHaveAttribute('role', 'status');
});
