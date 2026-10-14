import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { WarmingAccountState, WarmingBoardState } from '@/shared/api';

import { WarmingPage } from './WarmingPage';
import { BOARD, jsonResponse, renderWithClient } from './WarmingPage.testHelpers';

// A confirmed spam block: warming stopped, the card asks «Удалить» or «Оставить».

const BLOCKED: WarmingAccountState = {
  account_id: 'blocked',
  label: 'Blocked',
  state: 'spam_blocked',
  health: 'fail',
  cycles_completed: 4,
  spam_blocked_at: '2026-10-10T00:00:00+00:00',
  attention: { code: 'spam_blocked' },
  wave: 2,
  started_at: '2026-10-08T00:00:00+00:00',
};

function routeWith(board: WarmingBoardState) {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/warming/board') return Promise.resolve(jsonResponse(board));
    if (url.pathname.endsWith('/delete-reason')) {
      return Promise.resolve(jsonResponse({ reason: 'retired' }));
    }
    if (url.pathname === '/api/v1/warming/waves/blocked') {
      return Promise.resolve(
        jsonResponse({
          account_id: 'blocked',
          waves: [
            {
              wave: 1,
              started_at: '2026-09-01T00:00:00+00:00',
              ended_at: '2026-09-15T00:00:00+00:00',
              end_reason: 'released',
              worked_days: 14,
              target_days: 14,
            },
            {
              wave: 2,
              started_at: '2026-10-08T00:00:00+00:00',
              ended_at: '2026-10-10T00:00:00+00:00',
              end_reason: 'spam_blocked',
              worked_days: 2,
              target_days: 14,
            },
          ],
        }),
      );
    }
    return Promise.resolve(jsonResponse({}));
  });
}

function calls(path: string): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => new URL(request.url).pathname === path);
}

test('the stopped card names the block and has no Stop, only Delete and Keep', async () => {
  routeWith({ ...BOARD, warming: [BLOCKED] });
  renderWithClient(<WarmingPage />);

  expect(await screen.findByText(/@SpamBot подтвердил спамблок/)).toBeInTheDocument();
  expect(screen.getAllByText('Спамблок').length).toBeGreaterThan(0);
  expect(screen.queryByRole('button', { name: 'Стоп' })).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'Оставить' }));
  await waitFor(() => {
    expect(calls('/api/v1/warming/spam-block/keep')).toHaveLength(1);
  });
  expect(await calls('/api/v1/warming/spam-block/keep')[0]?.clone().json()).toEqual({
    account_id: 'blocked',
  });
});

test('«Удалить» opens the account delete dialog and deletes only on its confirmation', async () => {
  routeWith({ ...BOARD, warming: [BLOCKED] });
  renderWithClient(<WarmingPage />);

  await userEvent.click(await screen.findByRole('button', { name: 'Удалить' }));
  const dialog = await screen.findByRole('dialog', { name: /Удалить аккаунт/ });
  expect(calls('/api/v1/accounts/blocked')).toHaveLength(0);
  await userEvent.click(within(dialog).getByRole('button', { name: 'Удалить' }));

  await waitFor(() => {
    const deletes = calls('/api/v1/accounts/blocked').filter((r) => r.method === 'DELETE');
    expect(deletes).toHaveLength(1);
  });
});

test('a second wave wears its chip, and the chip opens the wave history', async () => {
  routeWith({ ...BOARD, warming: [BLOCKED] });
  renderWithClient(<WarmingPage />);

  await userEvent.click(await screen.findByRole('button', { name: /^Волна 2 · с / }));

  const dialog = await screen.findByRole('dialog', { name: 'Волны прогрева · blocked' });
  expect(await within(dialog).findByText('остановлен: спамблок', { exact: false })).toBeVisible();
  expect(within(dialog).getByText(/14 дней из 14/)).toBeInTheDocument();
});

test('a kept account in the ready column keeps its badge', async () => {
  const kept: WarmingAccountState = {
    ...BLOCKED,
    account_id: 'kept',
    label: 'Kept',
    state: 'idle',
    health: 'idle',
    attention: null,
    readiness: { ready: true, reasons: [] },
  };
  routeWith({ ...BOARD, idle: [kept], warming: [] });
  renderWithClient(<WarmingPage />);

  expect(await screen.findByText('kept')).toBeInTheDocument();
  expect(screen.getByText('Спамблок')).toBeInTheDocument();
});
