import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { WarmingAccountState } from '@/shared/api';

import { WarmingBoard } from './WarmingBoard';

// The screen itself is covered in features/phone-screen; here it only has to open.
vi.mock('@/features/phone-screen', () => ({
  PhoneScreen: ({ phoneId, mode }: { phoneId: string; mode: string }) => (
    <div>{`screen ${phoneId} ${mode}`}</div>
  ),
}));

function account(id: string, livePhoneId: string | null): WarmingAccountState {
  return {
    account_id: id,
    label: id,
    state: 'active',
    health: 'ok',
    cycles_completed: 1,
    device_mode: 'mobile',
    live_phone_id: livePhoneId,
  };
}

test('only a card whose phone is in a visit can be watched, and only watched', async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <WarmingBoard
        warming={[account('acc-live', 'telebuba-acc-live'), account('acc-resting', null)]}
        onStop={vi.fn()}
        onPromote={vi.fn()}
        busyIds={new Set<string>()}
      />
    </QueryClientProvider>,
  );

  expect(screen.getAllByRole('button', { name: 'Смотреть экран телефона' })).toHaveLength(1);
  await userEvent.click(screen.getByRole('button', { name: 'Смотреть экран телефона' }));

  expect(screen.getByText('screen telebuba-acc-live view')).toBeTruthy();
});

test('the visit ending leaves the open screen be, and the next visit opens nothing by itself', async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const board = (livePhoneId: string | null) => (
    <QueryClientProvider client={queryClient}>
      <WarmingBoard
        warming={[account('acc-live', livePhoneId)]}
        onStop={vi.fn()}
        onPromote={vi.fn()}
        busyIds={new Set<string>()}
      />
    </QueryClientProvider>
  );
  const { rerender } = render(board('telebuba-acc-live'));
  await userEvent.click(screen.getByRole('button', { name: 'Смотреть экран телефона' }));

  rerender(board(null));
  expect(screen.getByText('screen telebuba-acc-live view')).toBeTruthy();
  await userEvent.keyboard('{Escape}');
  rerender(board('telebuba-acc-live'));

  expect(screen.queryByText('screen telebuba-acc-live view')).toBeNull();
});

test('a phone card lists only the steps its visit runs', () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <WarmingBoard
        warming={[
          {
            ...account('acc-phone', null),
            mechanics: { join: false, reactions: true, stories: true, dialogue: false },
          },
        ]}
        onStop={vi.fn()}
        onPromote={vi.fn()}
        busyIds={new Set<string>()}
      />
    </QueryClientProvider>,
  );

  expect(screen.getByText('Механики: чтение · реакции · сторис')).toBeTruthy();
});
