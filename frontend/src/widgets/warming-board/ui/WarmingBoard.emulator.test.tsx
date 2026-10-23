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

function preparing(id: string, phonePrep: WarmingAccountState['phone_prep']): WarmingAccountState {
  return { ...account(id, false), phone_prep: phonePrep };
}

test('a phone being prepared, or one that failed, says so on its card', () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <WarmingBoard
        warming={[
          preparing('acc-prep', { status: 'preparing' }),
          preparing('acc-fail', { status: 'failed', code: 'emulator_binder_missing' }),
          preparing('acc-ready', null),
        ]}
        onStop={vi.fn()}
        onPromote={vi.fn()}
        busyIds={new Set<string>()}
      />
    </QueryClientProvider>,
  );

  expect(screen.getAllByText(/Готовим телефон/)).toHaveLength(1);
  const failed = screen.getAllByText(/Телефон не готов: В ядре сервера нет binder/);
  expect(failed).toHaveLength(1);
  expect(failed[0]).toHaveAttribute('role', 'status');
});

test('a phone with no channel to read says so on its card', () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <WarmingBoard
        warming={[
          { ...account('acc-empty', false), phone_no_channels: true },
          account('acc-reading', false),
        ]}
        onStop={vi.fn()}
        onPromote={vi.fn()}
        busyIds={new Set<string>()}
      />
    </QueryClientProvider>,
  );

  const notes = screen.getAllByText(/На телефоне нет каналов для чтения/);
  expect(notes).toHaveLength(1);
  expect(notes[0]).toHaveAttribute('role', 'status');
});
