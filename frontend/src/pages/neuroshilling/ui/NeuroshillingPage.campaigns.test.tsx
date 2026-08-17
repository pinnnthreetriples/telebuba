import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import {
  BOARD,
  callsTo,
  ECHOED,
  FULL_CAMPAIGN,
  renderPage,
  routeApi,
  SCENARIO,
} from './NeuroshillingPage.testHelpers';

test('the first campaign is selected by default and its roster is shown', async () => {
  routeApi();
  renderPage();

  expect(await screen.findByText('Промо')).toBeInTheDocument();
  await waitFor(() => {
    expect(screen.getByText('Выбрано: 1')).toBeInTheDocument();
  });
  // Only the rostered account reaches the card; the rest of the pool stays in the picker.
  expect(screen.getByText('Алиса')).toBeInTheDocument();
  expect(screen.queryByText('Борис')).not.toBeInTheDocument();
});

test('with no campaigns nothing scoped is fetched and the empty state stands alone', async () => {
  routeApi([]);
  renderPage();

  expect(await screen.findByText('Пока нет кампаний')).toBeInTheDocument();
  // `enabled: campaignId !== null` — a board read for the empty string would 404.
  expect(
    vi.mocked(fetch).mock.calls.some(([input]) => (input as Request).url.includes('/board')),
  ).toBe(false);
  expect(screen.queryByText('Выбрать аккаунты')).not.toBeInTheDocument();
});

test('creating a campaign posts the name and selects what came back', async () => {
  routeApi();
  renderPage();
  expect(await screen.findByText('Промо')).toBeInTheDocument();

  await userEvent.click(screen.getByText('+ Создать кампанию'));
  await userEvent.type(screen.getByLabelText('Название кампании'), '  Новая  ');
  await userEvent.click(screen.getByText('Создать кампанию'));

  await waitFor(() => {
    expect(callsTo('/api/v1/neuroshilling/campaigns', 'POST')).toHaveLength(1);
  });
  const posted = await callsTo('/api/v1/neuroshilling/campaigns', 'POST')[0]!.json();
  expect(posted).toEqual({ name: 'Новая' });

  // The new campaign becomes the selected one, so the board follows it.
  await waitFor(() => {
    expect(callsTo('/api/v1/neuroshilling/campaigns/c9/board', 'GET').length).toBeGreaterThan(0);
  });
});

test('the picker saves the whole roster in one PUT that echoes every other field back', async () => {
  routeApi([FULL_CAMPAIGN], SCENARIO, { ...BOARD, campaign: FULL_CAMPAIGN });
  renderPage();
  await waitFor(() => {
    expect(screen.getByText('Выбрать аккаунты')).toBeInTheDocument();
  });

  await userEvent.click(screen.getByText('Выбрать аккаунты'));
  await userEvent.click(screen.getAllByRole('button', { name: 'Добавить в кампанию' })[0]!);
  await userEvent.click(screen.getByText('Готово'));

  await waitFor(() => {
    expect(callsTo('/api/v1/neuroshilling/campaigns/c1', 'PUT')).toHaveLength(1);
  });
  const body = (await callsTo('/api/v1/neuroshilling/campaigns/c1', 'PUT')[0]!.json()) as Record<
    string,
    unknown
  >;
  // Compared WHOLE, not field by field: a PUT is a whole-form replacement, so a
  // field this body stops carrying is written back as its schema default (7 → 10,
  // 45 → 60, the topic and the targets emptied) — and the key simply going missing
  // is what an equality over the whole body catches.
  expect(body).toEqual({
    ...ECHOED,
    accounts: [
      { account_id: 'a1', role_id: null, is_reserve: false },
      { account_id: 'a2', role_id: null, is_reserve: false },
    ],
  });
});

test('the picker is not offered while the board holding the roster is still in flight', async () => {
  routeApi();
  const routed = vi.mocked(fetch).getMockImplementation()!;
  let landBoard!: () => void;
  const boardLanded = new Promise<void>((resolve) => {
    landBoard = resolve;
  });
  vi.mocked(fetch).mockImplementation((input, init) => {
    const request = input as Request;
    if (new URL(request.url).pathname.endsWith('/board')) {
      return boardLanded.then(() => routed(input, init));
    }
    return routed(input, init);
  });
  renderPage();
  expect(await screen.findByText('Промо')).toBeInTheDocument();

  // The campaign list is up and a campaign is chosen, but the roster behind the
  // picker has not arrived. Opening it here would seed an empty draft over a
  // campaign that has accounts, and «Готово» would save that emptiness back.
  expect(screen.queryByText('Выбрать аккаунты')).not.toBeInTheDocument();

  landBoard();

  expect(await screen.findByText('Выбрать аккаунты')).toBeInTheDocument();
  expect(screen.getByText('Выбрано: 1')).toBeInTheDocument();
});

test('leaving the picker any way but «Готово» writes nothing', async () => {
  routeApi();
  renderPage();
  await waitFor(() => {
    expect(screen.getByText('Выбрать аккаунты')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Выбрать аккаунты'));
  await userEvent.click(screen.getAllByRole('button', { name: 'Добавить в кампанию' })[0]!);

  await userEvent.keyboard('{Escape}');

  // A running campaign refuses this PUT outright, so a write on the way out hands
  // the operator an error toast for opening the roster and closing it again.
  await waitFor(() => {
    expect(screen.queryByText('Готово')).not.toBeInTheDocument();
  });
  expect(callsTo('/api/v1/neuroshilling/campaigns/c1', 'PUT')).toHaveLength(0);
});

test('deleting a campaign confirms first, then DELETEs it', async () => {
  routeApi();
  renderPage();
  expect(await screen.findByText('Промо')).toBeInTheDocument();

  await userEvent.click(screen.getByLabelText('Удалить кампанию'));
  expect(screen.getByText('Удалить кампанию «Промо»?')).toBeInTheDocument();
  await userEvent.click(screen.getByText('Удалить'));

  await waitFor(() => {
    expect(callsTo('/api/v1/neuroshilling/campaigns/c1', 'DELETE')).toHaveLength(1);
  });
});
