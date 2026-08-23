import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
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

test('the accounts modal names the channel an account is permanently banned in', async () => {
  // The channel row still reads "Готов" (a sibling account posts there), which is
  // exactly why the ban has to reach the account list — the modal is where the only
  // remedy, adding another account, lives.
  routeApi({
    ...BOARD,
    accounts: [
      {
        ...BOARD.accounts[0],
        readiness: [
          { channel: '@news', ready: false, joined: true, captcha_passed: false, banned: true },
        ],
      },
    ],
  });
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  await userEvent.click(screen.getByLabelText('Аккаунты в нейрокомментинге'));
  expect(screen.getByText('Забанен навсегда: @news')).toBeInTheDocument();
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

test('a channel mutation invalidates this page only, not the whole cache', async () => {
  routeApi();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
  render(
    <QueryClientProvider client={queryClient}>
      <NeurocommentPage />
    </QueryClientProvider>,
  );
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });

  await userEvent.click(screen.getByLabelText('Убрать канал'));
  await userEvent.click(await screen.findByText('Убрать'));

  await waitFor(() => {
    expect(invalidate).toHaveBeenCalled();
  });
  // No mutation on this page touches an account row, a proxy, the warming board
  // or the settings, yet a bare invalidateQueries() refetched all of them plus
  // every open profile snapshot. The page's own scope is the predicate.
  for (const [filters] of invalidate.mock.calls) {
    expect(filters?.predicate).toBeDefined();
  }
});

test('removing a second channel does not swallow the first one s refresh', async () => {
  // removeChannel.mutate(vars, {onSettled}) is per pill on ONE shared hook, and
  // setChannelToRemove(null) closes the modal on the same tick — so a second
  // removal is one click away while the first is in flight. It took the slot, and
  // the first channel's feedback mark and invalidateNeuro() were dropped, leaving
  // the pill on screen unmarked. useLogEventStream masks this on a BUSY campaign
  // (a runtime event refreshes anyway) but not on an idle one.
  const board = {
    ...BOARD,
    channels: [
      { channel: '@news', status: 'ready', ready_accounts: 1, total_accounts: 1 },
      { channel: '@promo', status: 'ready', ready_accounts: 1, total_accounts: 1 },
    ],
  };
  const releases: ((response: Response) => void)[] = [];
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname.endsWith('/channels/remove')) {
      return new Promise((resolve) => {
        releases.push(resolve);
      });
    }
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(board));
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: false, active_channels: 0, listener_account_id: null }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  const boardGets = () =>
    vi
      .mocked(fetch)
      .mock.calls.filter(([input]) => new URL((input as Request).url).pathname.endsWith('/board'))
      .length;

  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByLabelText('Убрать канал')).toHaveLength(2);
  });

  for (const index of [0, 1]) {
    await userEvent.click(screen.getAllByLabelText('Убрать канал')[index]!);
    await userEvent.click(await screen.findByText('Убрать'));
  }
  await waitFor(() => {
    expect(releases).toHaveLength(2);
  });

  // @promo settles first; @news landing later must still refresh the board.
  const before = boardGets();
  releases[1]!(jsonResponse({}));
  await waitFor(() => {
    expect(boardGets()).toBeGreaterThan(before);
  });
  const afterSecond = boardGets();
  releases[0]!(jsonResponse({}));
  await waitFor(() => {
    expect(boardGets()).toBeGreaterThan(afterSecond);
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

test('the deleted tile sums the account cards, never the channel rows', async () => {
  // Accounts carry 3 + 2 deleted of 4 + 3 posted; the channel rows carry a deliberately
  // different 9. Reading the channels would total 9 and could outrun the comments tile —
  // a channel row vanishes when the operator unlinks it, its comments do not.
  routeApi({
    ...BOARD,
    channels: [{ ...BOARD.channels[0], deleted_recent: 9 }],
    accounts: [
      { ...BOARD.accounts[0], comments_today: 4, deleted_today: 3 },
      { ...BOARD.accounts[0], account_id: 'acc-2', comments_today: 3, deleted_today: 2 },
    ],
  });

  renderWithClient(<NeurocommentPage />);
  // The Odometer rolls a 0–9 column into place, so a tile's value is readable only as
  // its settled offset: value N sits at translateY(-N*1.1em).
  const tileValue = (label: string): string | undefined =>
    screen.getByText(label).parentElement?.querySelector<HTMLElement>('[style*="translateY"]')
      ?.style.transform;
  await waitFor(() => {
    expect(tileValue('Удалено')).toBe('translateY(-5.50em)');
  });
  expect(tileValue('Комментариев')).toBe('translateY(-7.70em)');
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

test('the channel chip carries the channel-scoped deleted count', async () => {
  // The board row's chip counts one (account, channel) pair over `posted`. This one is the
  // channel's own delivered-set count across every account — the number that has to explain
  // a back-off, including a deletion of a comment recorded `failed` mid-send, which no
  // account card can see. Narrowing the row chip must not take this one off the board.
  routeApi({
    ...BOARD,
    channels: [{ ...BOARD.channels[0], deleted_recent: 4 }],
    accounts: [{ ...BOARD.accounts[0], deleted_today: 0 }],
  });

  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getByText('4 удалено')).toBeInTheDocument();
  });
  // …and it sits on the channel PILL, not on a board row. Both wrappers are a span holding
  // the channel name plus the chip, so `textContent` alone cannot tell them apart — the
  // pill is the one carrying the remove button.
  const pill = screen.getByText('4 удалено').parentElement;
  expect(pill?.textContent).toContain('@news');
  expect(pill?.querySelector('button[aria-label="Убрать канал"]')).not.toBeNull();
});
