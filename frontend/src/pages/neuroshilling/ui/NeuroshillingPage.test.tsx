import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { NeuroshillingBoard, NeuroshillingCampaign } from '@/shared/api';

import { NeuroshillingPage } from './NeuroshillingPage';

const CAMPAIGN: NeuroshillingCampaign = {
  campaign_id: 'c1',
  name: 'Промо',
  mode: 'campaign',
  topic: 'про сервис',
  targets_raw: '@chat',
  status: 'idle',
  messages_per_hour: 7,
  listen_minutes: 45,
  created_at: 'now',
  updated_at: 'now',
};

const BOARD: NeuroshillingBoard = {
  campaign: CAMPAIGN,
  available: [
    { account_id: 'a1', title: 'Алиса', assigned: true },
    { account_id: 'a2', title: 'Борис' },
    { account_id: 'a3', title: 'Виктор', busy_owner: 'warming' },
  ],
  targets: ['@chat'],
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderPage(ui: ReactElement = <NeuroshillingPage />) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

// Routes the five stage-one endpoints; `campaigns` lets a test start from an
// empty account of the world.
function routeApi(campaigns: NeuroshillingCampaign[] = [CAMPAIGN]): void {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neuroshilling/campaigns') {
      if (request.method === 'POST') {
        return Promise.resolve(jsonResponse({ ...CAMPAIGN, campaign_id: 'c9', name: 'Новая' }));
      }
      return Promise.resolve(jsonResponse({ campaigns }));
    }
    if (url.pathname.endsWith('/board')) {
      return Promise.resolve(jsonResponse(BOARD));
    }
    if (request.method === 'DELETE') return Promise.resolve(new Response(null, { status: 204 }));
    return Promise.resolve(jsonResponse(CAMPAIGN));
  });
}

function callsTo(pathname: string, method: string): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => new URL(request.url).pathname === pathname && request.method === method);
}

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

test('the picker saves the whole roster in one PUT that echoes the campaign back', async () => {
  routeApi();
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
  expect(body.accounts).toEqual([
    { account_id: 'a1', role_id: null, is_reserve: false },
    { account_id: 'a2', role_id: null, is_reserve: false },
  ]);
  // A PUT is a whole-form replacement: anything omitted here would be written
  // back as its schema default (7 → 10, 45 → 60, the topic and targets emptied).
  expect(body.messages_per_hour).toBe(7);
  expect(body.listen_minutes).toBe(45);
  expect(body.topic).toBe('про сервис');
  expect(body.targets_raw).toBe('@chat');
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

test('a log-stream frame refetches this page s queries', async () => {
  routeApi();
  renderPage();
  await waitFor(() => {
    expect(screen.getByText('Выбрано: 1')).toBeInTheDocument();
  });
  const before = callsTo('/api/v1/neuroshilling/campaigns', 'GET').length;

  const stream = (
    globalThis.EventSource as unknown as { last(): { emit(d: unknown): void } }
  ).last();
  stream.emit({ id: 1, ts: 'now', level: 'info', event: 'neuroshilling_started' });

  await waitFor(
    () => {
      expect(callsTo('/api/v1/neuroshilling/campaigns', 'GET').length).toBeGreaterThan(before);
    },
    { timeout: 3000 },
  );
});
