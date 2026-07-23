import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import {
  BOARD,
  CAMPAIGN,
  jsonResponse,
  renderWithClient,
  routeApi,
} from './NeurocommentPage.testHelpers';
import { NeurocommentPage } from './NeurocommentPage';

test('renders campaigns and the board for the selected campaign', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  expect(screen.getByText('Готов')).toBeInTheDocument();
  expect(screen.getAllByText('Promo').length).toBeGreaterThan(0);
});

test('the gear in the board header opens the accounts modal', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  await userEvent.click(screen.getByLabelText('Аккаунты в нейрокомментинге'));
  expect(screen.getByText('Готово')).toBeInTheDocument();
});

test('removing a campaign channel asks for confirmation, then calls the deactivate endpoint', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  await userEvent.click(screen.getByLabelText('Убрать канал'));
  const removeConfirm = await screen.findByText('Убрать');
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/channels/remove')),
  ).toBe(false);
  await userEvent.click(removeConfirm);
  await waitFor(() => {
    const removed = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/channels/remove'));
    expect(removed).toBe(true);
  });
});

test('the add-channel pill reveals an input and adds the channel', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });

  await userEvent.click(screen.getByText('+ Канал'));
  const input = screen.getByPlaceholderText(/Введите|@|канал/i);
  await userEvent.type(input, '@promo');
  // The add button shares its aria-label with the modal's add ("Добавить").
  await userEvent.click(screen.getByRole('button', { name: 'Добавить' }));
  await waitFor(() => {
    const linked = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/channels'));
    expect(linked).toBe(true);
  });
});

test('checking channels colours banned chips red and healthy chips green', async () => {
  const board2 = {
    ...BOARD,
    channels: [
      { channel: '@news', status: 'ready', ready_accounts: 1, total_accounts: 1 },
      { channel: '@promo', status: 'ready', ready_accounts: 1, total_accounts: 1 },
    ],
  };
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname.endsWith('/channel-bans') && request.method === 'POST') {
      return Promise.resolve(
        jsonResponse({
          items: [
            { channel: '@news', status: 'banned' },
            { channel: '@promo', status: 'ok' },
          ],
        }),
      );
    }
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(board2));
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: false, active_channels: 0, listener_account_id: null }),
      );
    }
    if (url.pathname === '/api/v1/accounts') {
      return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
    }
    return Promise.resolve(jsonResponse({}));
  });

  const chip = (channel: string): HTMLElement | null =>
    screen
      .getAllByLabelText('Убрать канал')
      .map((btn) => btn.closest('span'))
      .find((span) => span?.textContent?.includes(channel)) ?? null;

  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getByText('Проверить каналы')).toBeInTheDocument();
  });
  await waitFor(() => {
    expect(chip('@news')).not.toBeNull();
  });

  await userEvent.click(screen.getByText('Проверить каналы'));

  await waitFor(() => {
    expect(chip('@news')?.className).toContain('text-danger');
  });
  expect(chip('@promo')?.className).toContain('text-[#2e9e64]');
});
