import { screen, waitFor, within } from '@testing-library/react';
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
    expected_updated_at: FULL_CAMPAIGN.updated_at,
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

test('starting another campaign first selects its settings instead of launching unseen dialogue', async () => {
  routeApi([CAMPAIGN, SECOND_CAMPAIGN]);
  renderPage();
  await userEvent.click((await screen.findAllByRole('button', { name: 'Действия кампании' }))[1]!);
  await userEvent.click(screen.getAllByRole('button', { name: 'Запустить кампанию' })[1]!);
  await waitFor(() => {
    expect(callsTo('/api/v1/neuroshilling/campaigns/c2/settings', 'GET').length).toBeGreaterThan(0);
  });
  expect(callsTo('/api/v1/neuroshilling/campaigns/c2/start', 'POST')).toHaveLength(0);
});

test('the selected campaign row starts with its frozen settings version', async () => {
  routeApi();
  renderPage();
  await waitFor(() => {
    expect(callsTo('/api/v1/neuroshilling/campaigns/c1/settings', 'GET')).toHaveLength(1);
  });
  await userEvent.click((await screen.findAllByRole('button', { name: 'Действия кампании' }))[0]!);
  await userEvent.click(screen.getByRole('button', { name: 'Запустить кампанию' }));
  await waitFor(() => {
    expect(callsTo('/api/v1/neuroshilling/campaigns/c1/start', 'POST')).toHaveLength(1);
  });
  expect(await callsTo('/api/v1/neuroshilling/campaigns/c1/start', 'POST')[0]!.json()).toEqual({
    expected_updated_at: 'now',
  });
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

// Several role cards each carry a full account list, so an option is looked up inside
// the list its own trigger controls.
async function pickAccount(label: string, name: RegExp) {
  const trigger = await screen.findByLabelText(label);
  await userEvent.click(trigger);
  const list = document.getElementById(trigger.getAttribute('aria-controls')!)!;
  await userEvent.click(within(list).getByRole('option', { name }));
}

// A role added with «+ Добавить роль» exists only in the draft, under a `role-N` key,
// until the dialogue is saved; the roster PUT checks role ids against STORED roles, so
// the pick has to travel with the combined save instead.
function routeSavedRole() {
  routeApi();
  const routed = vi.mocked(fetch).getMockImplementation()!;
  const savedScenario = {
    ...SCENARIO,
    roles: [...SCENARIO.roles!, { role_id: 'r2', name: '', description: '', created_at: 'now' }],
  };
  const savedAccounts = [
    { account_id: 'a1', role_id: null, is_reserve: false },
    { account_id: 'a2', role_id: 'r2', is_reserve: false },
  ];
  let saved = false;
  vi.mocked(fetch).mockImplementation((input, init) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/neuroshilling/campaigns/c1/settings') {
      if (request.method === 'PUT') saved = true;
      if (request.method === 'PUT' || saved) {
        return Promise.resolve(
          jsonResponse({
            campaign: { ...CAMPAIGN, updated_at: 'v2' },
            scenario: savedScenario,
            accounts: savedAccounts,
          }),
        );
      }
    }
    if (pathname.endsWith('/board') && saved) {
      return Promise.resolve(
        jsonResponse({
          ...BOARD,
          available: [
            { account_id: 'a1', title: 'Алиса', assigned: true },
            { account_id: 'a2', title: 'Борис', assigned: true, role_id: 'r2' },
            { account_id: 'a3', title: 'Виктор', busy_owner: 'warming' },
          ],
        }),
      );
    }
    return routed(input, init);
  });
}

test('picking an account for an unsaved role saves the dialogue and the roster together', async () => {
  routeSavedRole();
  renderPage();
  await openSettings();
  await userEvent.click(await screen.findByText('+ Добавить роль'));
  await pickAccount('Аккаунт роли 2', /Борис/);

  await waitFor(() => {
    expect(callsTo('/api/v1/neuroshilling/campaigns/c1/settings', 'PUT')).toHaveLength(1);
  });
  // No roster PUT: it would be refused with `unknown_role` for the draft key.
  expect(callsTo('/api/v1/neuroshilling/campaigns/c1', 'PUT')).toHaveLength(0);
  const body = (await callsTo('/api/v1/neuroshilling/campaigns/c1/settings', 'PUT')[0]!.json()) as {
    campaign: { accounts: { account_id: string; role_id: string | null }[] };
    scenario: { roles: { role_id: string | null }[] };
  };
  const draftKey = body.scenario.roles[1]!.role_id;
  expect(draftKey).toMatch(/^role-\d+$/);
  expect(body.campaign.accounts).toContainEqual({
    account_id: 'a2',
    role_id: draftKey,
    is_reserve: false,
  });

  // The server id replaced the draft key, so the next pick is an ordinary roster PUT.
  await waitFor(() => {
    expect(screen.getByLabelText('Аккаунт роли 2')).toHaveTextContent('Борис');
  });
  await pickAccount('Аккаунт роли 2', /Алиса/);
  await waitFor(() => {
    expect(callsTo('/api/v1/neuroshilling/campaigns/c1', 'PUT')).toHaveLength(1);
  });
  const roster = (await callsTo('/api/v1/neuroshilling/campaigns/c1', 'PUT')[0]!.json()) as {
    accounts: { account_id: string; role_id: string | null }[];
  };
  expect(roster.accounts).toEqual([{ account_id: 'a1', role_id: 'r2', is_reserve: false }]);
  expect(callsTo('/api/v1/neuroshilling/campaigns/c1/settings', 'PUT')).toHaveLength(1);
});

test('picking an account for a stored role keeps the roster PUT and never saves the dialogue', async () => {
  routeApi();
  renderPage();
  await openSettings();
  await pickAccount('Аккаунт роли 1', /Борис/);

  await waitFor(() => {
    expect(callsTo('/api/v1/neuroshilling/campaigns/c1', 'PUT')).toHaveLength(1);
  });
  expect(callsTo('/api/v1/neuroshilling/campaigns/c1/settings', 'PUT')).toHaveLength(0);
});
