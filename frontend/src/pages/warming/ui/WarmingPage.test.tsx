import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { WarmingBoardState } from '@/shared/api';

import {
  account,
  BOARD,
  jsonResponse,
  lastEventSource,
  renderWithClient,
  routeApi,
} from './WarmingPage.testHelpers';

const { WarmingPage } = await import('./WarmingPage');

test('renders the board, channels and settings from live data', async () => {
  routeApi();
  renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getByText('idle-1')).toBeInTheDocument();
  });
  expect(screen.getByText('warm-1')).toBeInTheDocument();
  expect(screen.getByText('@news')).toBeInTheDocument();
});

test('disables warming for a not-ready account and shows the reason', async () => {
  routeApi();
  renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getByText('idle-2')).toBeInTheDocument();
  });
  const blocked = screen.getByText('Недоступен');
  expect(blocked).toBeDisabled();
  expect(blocked.getAttribute('title')).toBe('нет прокси');
});

test('ready card: phone flag sits with the number, proxy flag with the proxy type', async () => {
  const board: WarmingBoardState = {
    ...BOARD,
    idle: [
      {
        ...account('idle-2', 'idle'),
        trust_score: 73,
        phone_country: 'RU',
        proxy_country: 'ID',
        proxy_type: 'https',
      },
    ],
  };
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/warming/board') return Promise.resolve(jsonResponse(board));
    return Promise.resolve(jsonResponse({}));
  });
  const { container } = renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getByText('idle-2')).toBeInTheDocument();
  });
  expect(screen.getByText('73')).toBeInTheDocument();
  expect(screen.getByText('HTTPS')).toBeInTheDocument();
  // Two distinct flags: the phone country next to the number, the proxy exit
  // country next to the proxy type (not both crammed by the proxy label).
  const phoneFlag = container.querySelector('.fi-ru');
  const proxyFlag = container.querySelector('.fi-id');
  expect(phoneFlag).not.toBeNull();
  expect(proxyFlag).not.toBeNull();
  expect(phoneFlag?.parentElement?.textContent).toContain('idle-2');
  expect(proxyFlag?.parentElement?.textContent).toContain('HTTPS');
});

test('ready card: Telegram name on top, phone + flag on the line beneath', async () => {
  const board: WarmingBoardState = {
    ...BOARD,
    idle: [
      {
        ...account('idle-9', 'idle'),
        first_name: 'Maria',
        phone: '529672284791',
        phone_country: 'MX',
      },
    ],
  };
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/warming/board') return Promise.resolve(jsonResponse(board));
    return Promise.resolve(jsonResponse({}));
  });
  const { container } = renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getByText('Maria')).toBeInTheDocument();
  });
  // The phone drops to a subtitle and the country flag rides with it, not the name.
  expect(screen.getByText('529672284791')).toBeInTheDocument();
  const phoneFlag = container.querySelector('.fi-mx');
  expect(phoneFlag?.parentElement?.textContent).toContain('529672284791');
  expect(phoneFlag?.parentElement?.textContent).not.toContain('Maria');
});

test('ready card: shows the captured Telegram photo when an avatar etag is set', async () => {
  const board: WarmingBoardState = {
    ...BOARD,
    idle: [{ ...account('idle-pic', 'idle'), avatar_etag: 'v9' }],
  };
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/warming/board') return Promise.resolve(jsonResponse(board));
    return Promise.resolve(jsonResponse({}));
  });
  const { container } = renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getByText('idle-pic')).toBeInTheDocument();
  });
  const img = container.querySelector('img');
  expect(img?.getAttribute('src')).toBe('/api/v1/accounts/idle-pic/avatar?v=v9');
});

test('warmed card: shows the Telegram name, with the phone on the subtitle', async () => {
  // The warmed pool rides the board payload now, so the named account is seeded
  // there rather than on the retired /warmed fetch.
  const board: WarmingBoardState = {
    ...BOARD,
    warmed: [
      {
        account_id: 'grad-named',
        label: 'Graduate',
        warming_days: 20,
        first_name: 'Nadia',
        phone: '+79261112233',
        trust_score: 88,
        target_days: 14,
      },
    ],
  };
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/warming/board') return Promise.resolve(jsonResponse(board));
    return Promise.resolve(jsonResponse({}));
  });
  renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getByText('Nadia')).toBeInTheDocument();
  });
  // Named now → the phone drops to the subtitle beneath the name.
  expect(screen.getByText('+79261112233')).toBeInTheDocument();
});

test('shows graduated accounts and wires return-to-warming + handoff', async () => {
  // The warmed pool rides the board payload now (no separate /warmed fetch here).
  const board: WarmingBoardState = {
    ...BOARD,
    warmed: [
      {
        account_id: 'grad',
        label: 'Graduate',
        warming_days: 20,
        phone: '+79261112233',
        phone_country: 'RU',
        proxy_country: 'ID',
        proxy_type: 'socks5',
        trust_score: 88,
        target_days: 14,
      },
    ],
  };
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/warming/board') return Promise.resolve(jsonResponse(board));
    return Promise.resolve(jsonResponse({}));
  });
  const { container } = renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getByText('+79261112233')).toBeInTheDocument();
  });
  expect(screen.getByText('SOCKS5')).toBeInTheDocument();
  // Warmed card: phone country flag sits with the number, proxy exit country
  // flag with the proxy type — not the phone flag crammed by the proxy label.
  const phoneFlag = container.querySelector('.fi-ru');
  const proxyFlag = container.querySelector('.fi-id');
  expect(phoneFlag?.parentElement?.textContent).toContain('+79261112233');
  expect(proxyFlag?.parentElement?.textContent).toContain('SOCKS5');

  await userEvent.click(screen.getByLabelText('Обратно в прогрев'));
  await waitFor(() => {
    const unpromoted = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/warming/unpromote'));
    expect(unpromoted).toBe(true);
  });

  await userEvent.click(screen.getByText('В нейрокомментинг'));
  await waitFor(() => {
    const handed = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/warming/handoff'));
    expect(handed).toBe(true);
  });
});

test('a handed-off account disappears from the warmed card', async () => {
  const board: WarmingBoardState = {
    ...BOARD,
    warmed: [
      {
        account_id: 'gone',
        label: 'Gone',
        warming_days: 20,
        phone: '+70001112233',
        trust_score: 90,
        target_days: 14,
        nc_handed_off: true,
      },
    ],
  };
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/warming/board') return Promise.resolve(jsonResponse(board));
    return Promise.resolve(jsonResponse({}));
  });
  renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getByText('idle-1')).toBeInTheDocument();
  });
  // Handed off → lives on the neurocomment page now, not the warmed card here.
  expect(screen.queryByText('+70001112233')).not.toBeInTheDocument();
});

test('refetches the board on a live SSE event', async () => {
  routeApi();
  renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getByText('idle-1')).toBeInTheDocument();
  });
  const boardCalls = () =>
    vi
      .mocked(fetch)
      .mock.calls.filter(([input]) => (input as Request).url.includes('/warming/board')).length;
  const before = boardCalls();
  act(() => {
    lastEventSource()?.emit({ id: 1, event: 'cycle_started' });
  });
  await waitFor(() => {
    expect(boardCalls()).toBeGreaterThan(before);
  });
});

test('a live SSE event refetches an expanded card activity log', async () => {
  // Pins 'listLogs' in WARMING_QUERY_IDS: without it the terminal keeps showing the
  // rows it loaded on expand, and the "new events still show" promise is a lie.
  routeApi();
  renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getByText('warm-1')).toBeInTheDocument();
  });
  await userEvent.click(screen.getAllByText('Лог активности')[0]!);
  const logCalls = () =>
    vi
      .mocked(fetch)
      .mock.calls.filter(([input]) => new URL((input as Request).url).pathname === '/api/v1/logs')
      .length;
  await waitFor(() => {
    expect(logCalls()).toBeGreaterThan(0);
  });
  const before = logCalls();
  act(() => {
    lastEventSource()?.emit({ id: 1, event: 'warming_subscribe' });
  });
  await waitFor(() => {
    expect(logCalls()).toBeGreaterThan(before);
  });
});

test('starts an idle account', async () => {
  routeApi();
  renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getByText('idle-1')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Прогреть'));
  await userEvent.click(screen.getByText('Запустить прогрев'));
  let startCall: [unknown, ...unknown[]] | undefined;
  await waitFor(() => {
    startCall = vi
      .mocked(fetch)
      .mock.calls.find(([input]) => (input as Request).url.includes('/warming/start'));
    expect(startCall).toBeDefined();
  });
  // The day slider's value (default 7) must reach the backend — was dropped before.
  const body = (await (startCall![0] as Request).clone().json()) as { target_days?: number };
  expect(body.target_days).toBe(7);
});

test('removing a channel asks for confirmation, then calls the remove endpoint', async () => {
  routeApi();
  renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getByText('@news')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByLabelText('Удалить'));
  const confirm = await screen.findByText('Удалить', { selector: 'button' });
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/channels/remove')),
  ).toBe(false);
  await userEvent.click(confirm);
  await waitFor(() => {
    const removed = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/channels/remove'));
    expect(removed).toBe(true);
  });
});
