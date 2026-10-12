import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { StartWarmingRequest, WarmingAccountState, WarmingBoardState } from '@/shared/api';

import { account, BOARD, jsonResponse, renderWithClient } from './WarmingPage.testHelpers';

const { WarmingPage } = await import('./WarmingPage');

// The phone/proxy geo warning on the page (Прогрев 2.0, item 2.2).

function mismatched(id: string, accepted = false): WarmingAccountState {
  return {
    ...account(id, 'idle'),
    phone_country: 'MX',
    proxy_country: 'US',
    geo_status: 'mismatch',
    geo_accepted: accepted,
  };
}

function serveBoard(board: WarmingBoardState) {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/warming/board') return Promise.resolve(jsonResponse(board));
    return Promise.resolve(jsonResponse({}));
  });
}

function startBodies(): Promise<StartWarmingRequest[]> {
  return Promise.all(
    vi
      .mocked(fetch)
      .mock.calls.map(([input]) => input as Request)
      .filter((request) => new URL(request.url).pathname === '/api/v1/warming/start')
      .map((request) => request.clone().json() as Promise<StartWarmingRequest>),
  );
}

test('a mismatched account starts only after the tick, and the consent travels', async () => {
  serveBoard({ ...BOARD, idle: [mismatched('mx-1')], warming: [] });
  renderWithClient(<WarmingPage />);
  // The page banner counts the pair waiting for consent.
  expect(
    await screen.findByText('У 1 аккаунта гео номера и прокси не совпадает'),
  ).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'Прогреть' }));
  const dialog = await screen.findByRole('dialog');
  await userEvent.click(within(dialog).getByLabelText('Запустить всё равно'));
  await userEvent.click(within(dialog).getByRole('button', { name: 'Запустить прогрев' }));

  await waitFor(async () => {
    expect(await startBodies()).toHaveLength(1);
  });
  expect((await startBodies())[0]).toMatchObject({
    account_id: 'mx-1',
    accept_geo_mismatch: true,
  });
});

test('an accepted pair asks nothing and shows its grey badge', async () => {
  serveBoard({ ...BOARD, idle: [mismatched('mx-1', true)], warming: [] });
  renderWithClient(<WarmingPage />);

  expect(await screen.findByText('Гео ≠ (принято)')).toBeInTheDocument();
  expect(screen.queryByText(/гео номера и прокси не совпадает/)).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Прогреть' }));
  const dialog = await screen.findByRole('dialog');
  expect(within(dialog).queryByLabelText('Запустить всё равно')).toBeNull();
  await userEvent.click(within(dialog).getByRole('button', { name: 'Запустить прогрев' }));

  await waitFor(async () => {
    expect(await startBodies()).toHaveLength(1);
  });
  expect((await startBodies())[0]?.accept_geo_mismatch).toBe(false);
});

test('the pool can start only the accounts whose geo matches', async () => {
  serveBoard({
    ...BOARD,
    idle: [mismatched('mx-1'), account('ok-1', 'idle')],
    warming: [],
  });
  renderWithClient(<WarmingPage />);
  await userEvent.click(await screen.findByRole('button', { name: 'Запустить пул' }));
  const dialog = await screen.findByRole('dialog');
  expect(within(dialog).getByText('У 1 из 2 гео не совпадает')).toBeInTheDocument();

  await userEvent.click(within(dialog).getByRole('button', { name: 'Только совпадающие' }));

  await waitFor(async () => {
    expect(await startBodies()).toHaveLength(1);
  });
  expect((await startBodies())[0]).toMatchObject({
    account_id: 'ok-1',
    accept_geo_mismatch: false,
  });
});

test('«Запустить все» starts the whole pool with the one consent', async () => {
  serveBoard({
    ...BOARD,
    idle: [mismatched('mx-1'), account('ok-1', 'idle')],
    warming: [],
  });
  renderWithClient(<WarmingPage />);
  await userEvent.click(await screen.findByRole('button', { name: 'Запустить пул' }));
  const dialog = await screen.findByRole('dialog');

  await userEvent.click(within(dialog).getByRole('button', { name: 'Запустить все' }));

  await waitFor(async () => {
    expect(await startBodies()).toHaveLength(2);
  });
  const bodies = await startBodies();
  expect(bodies.map((body) => body.account_id).sort()).toEqual(['mx-1', 'ok-1']);
  expect(bodies.every((body) => body.accept_geo_mismatch === true)).toBe(true);
});
