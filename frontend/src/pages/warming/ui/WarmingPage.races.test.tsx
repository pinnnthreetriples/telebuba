import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { WarmingBoardState } from '@/shared/api';

import {
  account,
  BOARD,
  boardFetches,
  jsonResponse,
  renderWithClient,
  routeApi,
} from './WarmingPage.testHelpers';

const { WarmingPage } = await import('./WarmingPage');

// Settles the second-fired call, then the first, asserting that BOTH refresh the
// board: one useMutation is ONE callback slot, so whichever handler got taken
// over never ran and its list refresh was silently dropped.
async function expectBothRefresh(second: () => void, first: () => void): Promise<void> {
  act(second);
  await waitFor(() => {
    expect(boardFetches()).toBeGreaterThan(1);
  });
  const afterSecond = boardFetches();
  act(first);
  await waitFor(() => {
    expect(boardFetches()).toBeGreaterThan(afterSecond);
  });
}

// Parks every call to `pathname`, keyed by the account_id in its body, so a test
// can settle the accounts in any order.
function parkPerAccount(board: WarmingBoardState, pathname: string): Record<string, () => void> {
  const resolvers: Record<string, () => void> = {};
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/warming/board') return Promise.resolve(jsonResponse(board));
    if (url.pathname === pathname) {
      return request
        .clone()
        .json()
        .then(
          (body: { account_id: string }) =>
            new Promise<Response>((resolve) => {
              resolvers[body.account_id] = () => {
                resolve(jsonResponse({}));
              };
            }),
        );
    }
    return Promise.resolve(jsonResponse({}));
  });
  return resolvers;
}

// Parks every call to `pathname` in fire order, for endpoints with no account_id
// to key on (the channel list).
function parkInOrder(board: WarmingBoardState, pathname: string): ((response: Response) => void)[] {
  const releases: ((response: Response) => void)[] = [];
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/warming/board') return Promise.resolve(jsonResponse(board));
    if (url.pathname === pathname && request.method === 'POST') {
      return new Promise((resolve) => {
        releases.push(resolve);
      });
    }
    return Promise.resolve(jsonResponse({}));
  });
  return releases;
}

test('handing off a second account does not swallow the first one s refresh', async () => {
  // busyIds is per account, so the other warmed card stays live and fires the SAME
  // handoff mutation. With mutate()+onSettled the second call took over the
  // observer's one callback slot, and the first account's feedback mark and board
  // refresh were lost.
  const warmedAccount = (id: string) => ({
    account_id: id,
    label: id,
    warming_days: 20,
    target_days: 14,
    trust_score: 88,
  });
  const board: WarmingBoardState = {
    ...BOARD,
    warmed: [warmedAccount('grad-1'), warmedAccount('grad-2')],
  };
  let releaseFirst!: (response: Response) => void;
  let handoffs = 0;
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/warming/board') return Promise.resolve(jsonResponse(board));
    if (url.pathname.includes('/warming/handoff')) {
      handoffs += 1;
      if (handoffs === 1) {
        return new Promise((resolve) => {
          releaseFirst = resolve;
        });
      }
      return Promise.resolve(jsonResponse({}));
    }
    return Promise.resolve(jsonResponse({}));
  });

  renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getAllByText('В нейрокомментинг')).toHaveLength(2);
  });

  const beforeClicks = boardFetches();
  await userEvent.click(screen.getAllByText('В нейрокомментинг')[0] as HTMLElement);
  // Re-query: the first click re-rendered the warmed card.
  await userEvent.click(screen.getAllByText('В нейрокомментинг')[1] as HTMLElement);
  await waitFor(() => {
    expect(handoffs).toBe(2);
  });
  await waitFor(() => {
    expect(boardFetches()).toBeGreaterThan(beforeClicks);
  });

  const beforeFirstSettles = boardFetches();
  releaseFirst(jsonResponse({}));
  await waitFor(() => {
    expect(boardFetches()).toBeGreaterThan(beforeFirstSettles);
  });
});

test('disables the bulk pool button while a bulk operation is in flight', async () => {
  // /warming/stop hangs so the stop mutation stays pending after the click.
  let releaseStop: (() => void) | undefined;
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/warming/board') return Promise.resolve(jsonResponse(BOARD));
    if (url.pathname === '/api/v1/warming/stop') {
      return new Promise<Response>((resolve) => {
        releaseStop = () => {
          resolve(jsonResponse({}));
        };
      });
    }
    return Promise.resolve(jsonResponse({}));
  });
  renderWithClient(<WarmingPage />);
  // BOARD has a warming account, so the pool button is the bulk "stop" control.
  const bulk = await screen.findByText('Остановить пул');
  expect(bulk).not.toBeDisabled();
  await userEvent.click(bulk);
  await waitFor(() => {
    expect(bulk).toBeDisabled();
  });
  releaseStop?.();
});

test('keeps the bulk button disabled until the whole batch settles, even if the last call resolves first', async () => {
  // Two warming accounts: the bulk stop fires a per-account call for each on a
  // single mutation observer. Its isPending reflects only the LAST call, so if
  // the last-fired settles first the button would re-enable mid-batch — the bug.
  const board: WarmingBoardState = {
    ...BOARD,
    warming: [account('warm-1', 'active'), account('warm-2', 'active')],
  };
  const resolvers = parkPerAccount(board, '/api/v1/warming/stop');
  renderWithClient(<WarmingPage />);
  const bulk = await screen.findByText('Остановить пул');
  await userEvent.click(bulk);
  // Both per-account stop calls are now in flight.
  await waitFor(() => {
    expect(Object.keys(resolvers)).toHaveLength(2);
  });
  // The LAST-fired account settles first while the earlier one is still pending.
  act(() => {
    resolvers['warm-2']?.();
  });
  await waitFor(() => {
    // The observer's isPending has flipped false here — bulkBusy must hold the guard.
    expect(bulk).toBeDisabled();
  });
  // Only once the earlier call also settles does the whole batch complete.
  act(() => {
    resolvers['warm-1']?.();
  });
  await waitFor(() => {
    expect(bulk).not.toBeDisabled();
  });
});

test('a bulk stop disables every card, and the first response clears only its own', async () => {
  // busyId was ONE string, so a batch of N left only the LAST account's card
  // disabled: every other card stayed clickable while its own stop was in flight,
  // and the first response to land cleared the whole board's busy state.
  const board: WarmingBoardState = {
    ...BOARD,
    warming: [account('warm-1', 'active'), account('warm-2', 'active')],
  };
  const resolvers = parkPerAccount(board, '/api/v1/warming/stop');
  renderWithClient(<WarmingPage />);
  await userEvent.click(await screen.findByText('Остановить пул'));
  await waitFor(() => {
    expect(Object.keys(resolvers)).toHaveLength(2);
  });
  const stops = () => screen.getAllByText('Стоп');
  expect(stops()[0]).toBeDisabled();
  expect(stops()[1]).toBeDisabled();

  act(() => {
    resolvers['warm-1']?.();
  });
  await waitFor(() => {
    expect(stops()[0]).toBeEnabled();
  });
  expect(stops()[1]).toBeDisabled();
  act(() => {
    resolvers['warm-2']?.();
  });
});

test('warming a second account keeps the first card busy and still refreshes for it', async () => {
  // The ready card is a list too. A single busyId moved the spinner to the second
  // card and re-enabled the first mid-request; and WarmDaysModal fired
  // start.mutate(vars, {onSettled}) into the hook's ONE callback slot, so the
  // second confirm took it over and the first account's board refresh was lost.
  const board: WarmingBoardState = {
    ...BOARD,
    idle: [account('idle-1', 'idle'), account('idle-2', 'idle')],
    warming: [],
  };
  const resolvers = parkPerAccount(board, '/api/v1/warming/start');
  renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getByText('idle-2')).toBeInTheDocument();
  });

  for (const index of [0, 1]) {
    await userEvent.click(screen.getAllByText('Прогреть')[index]!);
    await userEvent.click(screen.getByText('Запустить прогрев'));
  }
  await waitFor(() => {
    expect(Object.keys(resolvers)).toHaveLength(2);
  });
  expect(screen.getAllByText('Прогреть')[0]).toBeDisabled();
  expect(screen.getAllByText('Прогреть')[1]).toBeDisabled();

  // idle-2 settles first; idle-1 landing later must still refresh the board.
  await expectBothRefresh(
    () => resolvers['idle-2']?.(),
    () => resolvers['idle-1']?.(),
  );
});

test('removing a second channel does not swallow the first one s refresh', async () => {
  // Same ONE-callback-slot trap on the channel pills: a second removal confirmed
  // while the first was in flight dropped the first channel's feedback mark and
  // its invalidate, so the pill sat there unmarked over a stale list.
  const board: WarmingBoardState = {
    ...BOARD,
    channels: {
      channels: [
        { channel: '@news', created_at: 'now' },
        { channel: '@more', created_at: 'now' },
      ],
    },
  };
  const releases = parkInOrder(board, '/api/v1/warming/channels/remove');
  renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getByText('@more')).toBeInTheDocument();
  });

  for (const index of [0, 1]) {
    await userEvent.click(screen.getAllByLabelText('Удалить')[index]!);
    await userEvent.click(await screen.findByText('Удалить', { selector: 'button' }));
  }
  await waitFor(() => {
    expect(releases).toHaveLength(2);
  });

  // @more settles first; @news landing later must still refresh the list.
  await expectBothRefresh(
    () => releases[1]!(jsonResponse({})),
    () => releases[0]!(jsonResponse({})),
  );
});

test('adding a second channel does not swallow the first one s refresh', async () => {
  // The add input stays open until its OWN settle clears it, so a second Enter is
  // reachable while the first add is still in flight. addChannels.mutate's handler
  // sat in the hook's ONE callback slot, so the second add took it over and the
  // first channel's mark, its input reset and its invalidate were all dropped.
  const releases = parkInOrder(BOARD, '/api/v1/warming/channels');
  renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getByText('@news')).toBeInTheDocument();
  });

  await userEvent.click(screen.getByText('+ Канал'));
  const input = screen.getByLabelText('t.me/канал или @канал');
  await userEvent.type(input, '@one');
  await userEvent.click(screen.getByLabelText('Добавить'));
  // The pill is still in edit mode with '@one' in it — retype for the second add.
  await userEvent.clear(input);
  await userEvent.type(input, '@two');
  await userEvent.click(screen.getByLabelText('Добавить'));
  await waitFor(() => {
    expect(releases).toHaveLength(2);
  });

  // @two settles first; @one landing later must still refresh the list.
  await expectBothRefresh(
    () => releases[1]!(jsonResponse({})),
    () => releases[0]!(jsonResponse({})),
  );
});

test('adds a channel', async () => {
  routeApi();
  renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getByText('@news')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('+ Канал'));
  await userEvent.type(screen.getByLabelText('t.me/канал или @канал'), '@more');
  await userEvent.click(screen.getByLabelText('Добавить'));
  await waitFor(() => {
    const added = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.endsWith('/warming/channels'));
    expect(added).toBe(true);
  });
});
