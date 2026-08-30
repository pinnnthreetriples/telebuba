import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import {
  BOARD,
  CAMPAIGN,
  callsTo,
  ECHOED,
  emitLogFrame,
  FULL_CAMPAIGN,
  jsonResponse,
  openSettings,
  renderPage,
  routeApi,
  SCENARIO,
  SECOND_CAMPAIGN,
  waitForRefetch,
} from './NeuroshillingPage.testHelpers';

test('the first campaign is selected by default and its roster is shown', async () => {
  routeApi();
  renderPage();

  expect(await screen.findAllByText('Промо')).not.toHaveLength(0);
  await openSettings();
  await waitFor(() => {
    expect(screen.getByLabelText('Аккаунт роли 1')).toBeInTheDocument();
  });
  // Ростер читается там же, где набирается, — в карточке роли. Весь пул предлагается
  // выбором, поэтому «Борис» здесь ЕСТЬ, просто не выбран.
  expect(screen.getAllByText('Алиса')).not.toHaveLength(0);
  expect(screen.getAllByText('Борис')).not.toHaveLength(0);
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
  expect(await screen.findAllByText('Промо')).not.toHaveLength(0);

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

test('choosing an account for a role enrols it and echoes every other field back', async () => {
  routeApi([FULL_CAMPAIGN], SCENARIO, { ...BOARD, campaign: FULL_CAMPAIGN });
  renderPage();
  await openSettings();
  const pick = await screen.findByLabelText('Аккаунт роли 1');

  await userEvent.click(pick);
  await userEvent.click(screen.getByRole('option', { name: 'Борис' }));

  await waitFor(() => {
    expect(callsTo('/api/v1/neuroshilling/campaigns/c1', 'PUT')).toHaveLength(1);
  });
  const body = (await callsTo('/api/v1/neuroshilling/campaigns/c1', 'PUT')[0]!.json()) as Record<
    string,
    unknown
  >;
  // Compared WHOLE, not field by field: a PUT is a whole-form replacement, so a
  // field this body stops carrying is written back as its schema default — and the
  // key simply going missing is what an equality over the whole body catches.
  expect(body).toEqual({
    ...ECHOED,
    // «Алиса» уже была в ростере и роли не держала — назначение её не трогает: из
    // ростера выбывает только тот, у кого роль ЗАБРАЛИ.
    accounts: [
      { account_id: 'a1', role_id: null, is_reserve: false },
      { account_id: 'a2', role_id: 'r1', is_reserve: false },
    ],
  });
});

test('the settings are not offered while the board holding the roster is still in flight', async () => {
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
  expect(await screen.findAllByText('Промо')).not.toHaveLength(0);

  // The campaign list is up and a campaign is chosen, but the roster behind the
  // picker has not arrived. Opening it here would seed an empty draft over a
  // campaign that has accounts, and «Готово» would save that emptiness back.
  //
  // Гарантия та же, но теперь она структурная: ростер живёт в диалоге настроек, а диалог
  // не открывается, пока доска не приехала. Карандаш нажимается — и не открывает ничего.
  await openSettings();
  expect(screen.queryByLabelText('Аккаунт роли 1')).not.toBeInTheDocument();

  landBoard();

  await openSettings();
  expect(await screen.findByLabelText('Аккаунт роли 1')).toBeInTheDocument();
});

test('a selection the campaign list no longer carries falls back to the first campaign', async () => {
  let listed = [CAMPAIGN, SECOND_CAMPAIGN];
  routeApi(listed);
  const routed = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation((input, init) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neuroshilling/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: listed }));
    }
    const scoped = /campaigns\/([^/]+)\/board$/.exec(url.pathname);
    if (scoped !== null) {
      const campaign = listed.find((item) => item.campaign_id === (scoped[1] ?? ''));
      // A campaign that is gone has no board either.
      return Promise.resolve(
        campaign === undefined
          ? new Response(null, { status: 404 })
          : jsonResponse({ ...BOARD, campaign }),
      );
    }
    return routed(input, init);
  });
  renderPage();
  // Выбирает кнопка во всю карточку сайдбара — имя ей даёт `aria-label`, потому что
  // видимый текст лежит в слое, не принимающем нажатий.
  await userEvent.click(await screen.findByRole('button', { name: 'Вторая' }));
  await waitFor(() => {
    expect(callsTo('/api/v1/neuroshilling/campaigns/c2/board', 'GET').length).toBeGreaterThan(0);
  });

  // Deleted from another tab: the next list comes back without it.
  listed = [CAMPAIGN];
  const dropped = callsTo('/api/v1/neuroshilling/campaigns', 'GET').length;
  emitLogFrame();
  await waitForRefetch(dropped);

  // Proven on the frame AFTER the one that dropped it: that frame's board read was
  // already in flight when the shorter list landed.
  const gone = callsTo('/api/v1/neuroshilling/campaigns/c2/board', 'GET').length;
  const alive = callsTo('/api/v1/neuroshilling/campaigns/c1/board', 'GET').length;
  const before = callsTo('/api/v1/neuroshilling/campaigns', 'GET').length;
  emitLogFrame();
  await waitForRefetch(before);

  // Taken on trust, the selection keeps every scoped read pointed at a campaign the
  // server answers 404 for, on every frame, with nothing on screen to say so.
  await waitFor(() => {
    expect(callsTo('/api/v1/neuroshilling/campaigns/c1/board', 'GET').length).toBeGreaterThan(
      alive,
    );
  });
  expect(callsTo('/api/v1/neuroshilling/campaigns/c2/board', 'GET')).toHaveLength(gone);
});

test('deleting a campaign confirms first, then DELETEs it', async () => {
  routeApi();
  renderPage();
  expect(await screen.findAllByText('Промо')).not.toHaveLength(0);

  await userEvent.click(screen.getByLabelText('Удалить кампанию'));
  expect(screen.getByText('Удалить кампанию «Промо»?')).toBeInTheDocument();
  await userEvent.click(screen.getByText('Удалить'));

  await waitFor(() => {
    expect(callsTo('/api/v1/neuroshilling/campaigns/c1', 'DELETE')).toHaveLength(1);
  });
});
