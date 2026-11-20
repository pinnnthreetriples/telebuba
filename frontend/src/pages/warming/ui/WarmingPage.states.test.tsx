import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { WarmingBoardState } from '@/shared/api';

import { BOARD, jsonResponse, renderWithClient } from './WarmingPage.testHelpers';

const { WarmingPage } = await import('./WarmingPage');

// The page's own states: first load, a failed load, a failed poll over loaded data,
// nothing to warm at all, and the bar a multi-select brings up.

function serve(board: WarmingBoardState) {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/warming/board') return Promise.resolve(jsonResponse(board));
    return Promise.resolve(jsonResponse({}));
  });
}

test('the first load draws the page skeleton, not a bare «loading» line', () => {
  vi.mocked(fetch).mockImplementation(() => new Promise<Response>(() => undefined));
  const { container } = renderWithClient(<WarmingPage />);
  expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
  expect(screen.getByRole('heading', { name: 'Прогрев аккаунтов' })).toBeInTheDocument();
});

test('a failed first load says so and retries on request', async () => {
  let fail = true;
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/warming/board') {
      return Promise.resolve(fail ? jsonResponse({}, 500) : jsonResponse(BOARD));
    }
    return Promise.resolve(jsonResponse({}));
  });
  renderWithClient(<WarmingPage />);
  const retry = await screen.findByRole('button', { name: 'Повторить' });
  expect(screen.getByRole('alert')).toBeInTheDocument();
  fail = false;
  await userEvent.click(retry);
  expect(await screen.findByText('idle-1')).toBeInTheDocument();
});

test('a failed poll keeps the loaded board and warns the data is not fresh', async () => {
  serve(BOARD);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <WarmingPage />
    </QueryClientProvider>,
  );
  expect(await screen.findByText('idle-1')).toBeInTheDocument();

  vi.mocked(fetch).mockImplementation(() => Promise.resolve(jsonResponse({}, 500)));
  await act(async () => {
    await queryClient.refetchQueries();
  });

  await waitFor(() => {
    expect(screen.getByText(/Нет связи с сервером/)).toBeInTheDocument();
  });
  // The board is still there — one failed poll no longer wipes the page.
  expect(screen.getByText('idle-1')).toBeInTheDocument();
});

test('with nothing to warm at all the page explains where accounts come from', async () => {
  serve({ ...BOARD, idle: [], warming: [], warmed: [] });
  renderWithClient(<WarmingPage />);
  expect(await screen.findByText('Пока нечего прогревать')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'К аккаунтам' })).toBeInTheDocument();
});

test('picking ready accounts brings up the action bar, and closing it clears the pick', async () => {
  serve({ ...BOARD, settings: { ...BOARD.settings, enforce_readiness: false } });
  renderWithClient(<WarmingPage />);
  await userEvent.click(await screen.findByRole('checkbox', { name: 'Выбрать idle-1' }));

  const bar = screen.getByRole('toolbar', { name: 'Выбрано: 1' });
  expect(bar).toHaveTextContent('Прогреть · 1');

  await userEvent.click(screen.getByRole('button', { name: 'Снять' }));
  expect(screen.queryByRole('toolbar', { name: 'Выбрано: 1' })).toBeNull();
  expect(screen.getByRole('checkbox', { name: 'Выбрать idle-1' })).not.toBeChecked();
});
