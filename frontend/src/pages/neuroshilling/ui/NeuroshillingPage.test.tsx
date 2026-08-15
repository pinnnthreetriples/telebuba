import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type {
  NeuroshillingBoard,
  NeuroshillingCampaign,
  NeuroshillingScenario,
} from '@/shared/api';

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

const SCENARIO: NeuroshillingScenario = {
  campaign_id: 'c1',
  scenario_status: 'draft',
  roles: [{ role_id: 'r1', name: 'Скептик', description: 'сомневается', created_at: 'now' }],
  steps: [
    {
      step_id: 's1',
      position: 1,
      kind: 'message',
      role_id: 'r1',
      text: 'а работает вообще?',
      delay_min_seconds: 60,
      delay_max_seconds: 180,
    },
  ],
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

// Routes every endpoint the page reaches; `campaigns` lets a test start from an
// empty account of the world and `scenario` from a campaign with no dialogue.
function routeApi(
  campaigns: NeuroshillingCampaign[] = [CAMPAIGN],
  scenario: NeuroshillingScenario = SCENARIO,
): void {
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
    if (url.pathname.endsWith('/scenario')) {
      return Promise.resolve(jsonResponse(scenario));
    }
    if (url.pathname.endsWith('/generate')) {
      return Promise.resolve(
        jsonResponse({
          ...SCENARIO,
          steps: [{ ...SCENARIO.steps![0]!, step_id: 'g1', text: 'придуманная реплика' }],
        }),
      );
    }
    if (url.pathname.endsWith('/approve')) {
      return Promise.resolve(jsonResponse({ ...scenario, scenario_status: 'approved' }));
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

const SCENARIO_PATH = '/api/v1/neuroshilling/campaigns/c1/scenario';
const GENERATE_PATH = '/api/v1/neuroshilling/campaigns/c1/generate';
const APPROVE_PATH = '/api/v1/neuroshilling/campaigns/c1/approve';

function emitLogFrame(): void {
  const stream = (
    globalThis.EventSource as unknown as { last(): { emit(d: unknown): void } }
  ).last();
  stream.emit({ id: 1, ts: 'now', level: 'info', event: 'neuroshilling_started' });
}

async function waitForRefetch(before: number): Promise<void> {
  await waitFor(
    () => {
      expect(callsTo('/api/v1/neuroshilling/campaigns', 'GET').length).toBeGreaterThan(before);
    },
    { timeout: 3000 },
  );
}

test('a log-stream frame refetches this page s queries', async () => {
  routeApi();
  renderPage();
  await waitFor(() => {
    expect(screen.getByText('Выбрано: 1')).toBeInTheDocument();
  });
  const before = callsTo('/api/v1/neuroshilling/campaigns', 'GET').length;

  emitLogFrame();

  await waitForRefetch(before);
});

test('a log-stream frame leaves the scenario query alone', async () => {
  routeApi();
  renderPage();
  expect(await screen.findByLabelText('Тема')).toBeInTheDocument();
  const scenarioBefore = callsTo(SCENARIO_PATH, 'GET').length;
  const before = callsTo('/api/v1/neuroshilling/campaigns', 'GET').length;

  emitLogFrame();
  await waitForRefetch(before);

  // The form behind this query is explicit-save; the stream flushes on every log
  // row, so refetching it here is what would empty the form under the operator.
  expect(callsTo(SCENARIO_PATH, 'GET')).toHaveLength(scenarioBefore);
});

test('what the operator is typing survives the refetch a log frame drives', async () => {
  routeApi();
  renderPage();
  await userEvent.type(await screen.findByLabelText('Тема'), ' и доставку');
  const before = callsTo('/api/v1/neuroshilling/campaigns', 'GET').length;

  emitLogFrame();
  await waitForRefetch(before);

  // The board IS refetched, and it carries the campaign's stored topic. Resyncing
  // the form from it is the bug this page is arranged to avoid.
  expect(screen.getByLabelText('Тема')).toHaveValue('про сервис и доставку');
});

test('an approved campaign warns on the editing card before the approval dies', async () => {
  routeApi([CAMPAIGN], { ...SCENARIO, scenario_status: 'approved' });
  renderPage();
  await userEvent.type(await screen.findByLabelText('Тема'), '!');

  expect(screen.getByText('Сохранение снимет утверждение')).toBeInTheDocument();
  expect(
    screen.getByText('Есть несохранённые правки — в превью показан сохранённый сценарий.'),
  ).toBeInTheDocument();
});

test('saving writes the brief first and the dialogue second', async () => {
  routeApi();
  renderPage();
  await userEvent.type(await screen.findByLabelText('Тема'), '!');
  await userEvent.click(screen.getByText('Использовать сценарий'));

  await waitFor(() => {
    expect(callsTo(SCENARIO_PATH, 'PUT')).toHaveLength(1);
  });
  // The dialogue PUT always returns the campaign to `draft`, so it has to be the
  // LAST write of the pair.
  const puts = vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => request.method === 'PUT')
    .map((request) => new URL(request.url).pathname);
  expect(puts).toEqual(['/api/v1/neuroshilling/campaigns/c1', SCENARIO_PATH]);

  const brief = (await callsTo('/api/v1/neuroshilling/campaigns/c1', 'PUT')[0]!.json()) as Record<
    string,
    unknown
  >;
  expect(brief.topic).toBe('про сервис!');
  // Still a whole-form replacement: the fields other cards own are echoed back.
  expect(brief.messages_per_hour).toBe(7);
  expect(brief.targets_raw).toBe('@chat');

  const dialogue = (await callsTo(SCENARIO_PATH, 'PUT')[0]!.json()) as Record<string, unknown>;
  expect(dialogue.roles).toEqual([{ role_id: 'r1', name: 'Скептик', description: 'сомневается' }]);
  expect(dialogue.steps).toEqual([
    {
      kind: 'message',
      role_id: 'r1',
      text: 'а работает вообще?',
      reply_to_position: null,
      target_position: null,
      emoji: null,
      delay_min_seconds: 60,
      delay_max_seconds: 180,
    },
  ]);
});

test('regenerating over an existing dialogue confirms before it overwrites', async () => {
  routeApi();
  renderPage();
  await waitFor(() => {
    expect(screen.getByText('Перегенерировать')).toBeInTheDocument();
  });

  await userEvent.click(screen.getByText('Перегенерировать'));
  expect(screen.getByText('Сгенерировать новый диалог?')).toBeInTheDocument();
  // Nothing has been asked for yet — one click must not destroy the stored text.
  expect(callsTo(GENERATE_PATH, 'POST')).toHaveLength(0);

  await userEvent.click(screen.getByText('Сгенерировать'));
  await waitFor(() => {
    expect(callsTo(GENERATE_PATH, 'POST')).toHaveLength(1);
  });
  expect(await callsTo(GENERATE_PATH, 'POST')[0]!.json()).toEqual({
    persona_count: 3,
    step_count: 8,
  });
  // The answer replaces the form — that IS what the button means.
  await waitFor(() => {
    expect(screen.getByLabelText('Текст шага 1')).toHaveValue('придуманная реплика');
  });
});

test('a campaign with no dialogue generates without asking, and stores the topic first', async () => {
  routeApi([CAMPAIGN], { campaign_id: 'c1', scenario_status: 'draft', roles: [], steps: [] });
  renderPage();
  await waitFor(() => {
    expect(screen.getByText('Сгенерировать через ИИ')).toBeEnabled();
  });

  await userEvent.click(screen.getByText('Сгенерировать через ИИ'));

  await waitFor(() => {
    expect(callsTo(GENERATE_PATH, 'POST')).toHaveLength(1);
  });
  expect(screen.queryByText('Сгенерировать новый диалог?')).not.toBeInTheDocument();
  // The model is briefed from the STORED topic, so the PUT has to precede the ask.
  expect(callsTo('/api/v1/neuroshilling/campaigns/c1', 'PUT')).toHaveLength(1);
});

test('approving posts the approval on its own', async () => {
  routeApi();
  renderPage();
  await waitFor(() => {
    expect(screen.getByText('Утвердить')).toBeEnabled();
  });

  await userEvent.click(screen.getByText('Утвердить'));

  await waitFor(() => {
    expect(callsTo(APPROVE_PATH, 'POST')).toHaveLength(1);
  });
  expect(callsTo(SCENARIO_PATH, 'PUT')).toHaveLength(0);
});
