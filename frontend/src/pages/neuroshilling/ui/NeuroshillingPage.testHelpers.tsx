import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, waitFor } from '@testing-library/react';
import type { ReactElement } from 'react';
import { expect, vi } from 'vitest';

import '@/shared/i18n';

import type {
  NeuroshillingBoard,
  NeuroshillingCampaign,
  NeuroshillingCampaignUpdate,
  NeuroshillingScenario,
} from '@/shared/api';

import { NeuroshillingPage } from './NeuroshillingPage';

// The fixtures and the routing every NeuroshillingPage test file shares. Apart from
// the tests because one source file may not pass 700 lines
// (`tests/test_architecture.py::test_test_sources_stay_within_the_line_limit`), and
// the page has more behaviour than that.

export const CAMPAIGN: NeuroshillingCampaign = {
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

export const LAUNCHABLE_CAMPAIGN: NeuroshillingCampaign = {
  ...CAMPAIGN,
  scenario_status: 'approved',
  targets_raw: '@chat\n@other',
};

// Every field the roster PUT carries over from the campaign it is editing, each
// holding a value the schema would NOT default to — a field the page stops sending
// arrives as its default, which a fixture holding that default could not tell from
// a field that was sent. Typed against the request schema with nothing optional, so
// a field added to it stops this file compiling until it is listed here as well.
export const ECHOED: Omit<Required<NeuroshillingCampaignUpdate>, 'accounts'> = {
  name: 'Промо',
  mode: 'revive',
  topic: 'про сервис',
  targets_raw: '@chat',
  unique_messages: false,
  use_chat_context: true,
  media_message_link: 'https://t.me/c/1/2',
  media_step_position: 1,
  run_mode: 'parallel',
  pause_min_seconds: 11,
  pause_max_seconds: 21,
  messages_per_hour: 7,
  messages_per_chat_per_day: 4,
  total_per_account: 25,
  reserve_enabled: true,
  autoresponder: 'neurodialog',
  reply_to_humans: true,
  reply_activity: 'active',
  listen_minutes: 45,
};

export const FULL_CAMPAIGN: NeuroshillingCampaign = { ...CAMPAIGN, ...ECHOED };

export const SECOND_CAMPAIGN: NeuroshillingCampaign = {
  ...CAMPAIGN,
  campaign_id: 'c2',
  name: 'Вторая',
};

export const BOARD: NeuroshillingBoard = {
  campaign: CAMPAIGN,
  available: [
    { account_id: 'a1', title: 'Алиса', assigned: true },
    { account_id: 'a2', title: 'Борис' },
    { account_id: 'a3', title: 'Виктор', busy_owner: 'warming' },
  ],
  targets: ['@chat'],
};

export const SCENARIO: NeuroshillingScenario = {
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

export function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

export function renderPage(ui: ReactElement = <NeuroshillingPage />) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

// A campaign a Start would actually be allowed to begin: approved dialogue, two
// targets, and one account per role.
export const LAUNCHABLE_SCENARIO: NeuroshillingScenario = {
  ...SCENARIO,
  scenario_status: 'approved',
};
export const LAUNCHABLE_BOARD: NeuroshillingBoard = {
  campaign: LAUNCHABLE_CAMPAIGN,
  available: [
    { account_id: 'a1', title: 'Алиса', assigned: true, role_id: 'r1' },
    { account_id: 'a2', title: 'Борис', assigned: true, role_id: 'r1' },
  ],
  targets: ['@chat', '@other'],
  run: { status: 'idle', sent: 0, total: 2 },
};

// One row, so the panel has something to clear. Account-less on purpose: naming
// the account column is the terminal's own test, and a second "Алиса" on the page
// would only make the roster assertions ambiguous.
const LOG_ROW = {
  id: 7,
  created_at: '2026-07-11T10:00:00+00:00',
  level: 'INFO',
  status: 'success',
  account_id: null,
  event: 'neuroshilling_run_started',
  extra: {},
};

// Routes every endpoint the page reaches; `campaigns` lets a test start from an
// empty account of the world and `scenario` from a campaign with no dialogue.
export function routeApi(
  campaigns: NeuroshillingCampaign[] = [CAMPAIGN],
  scenario: NeuroshillingScenario = SCENARIO,
  board: NeuroshillingBoard = BOARD,
): void {
  // The list the POST below adds to. The page looks its selection up in this list,
  // so a creation that never joined it would read as a campaign that is gone.
  const listed = [...campaigns];
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/logs/count') {
      return Promise.resolve(jsonResponse({ matching: 412 }));
    }
    if (url.pathname === '/api/v1/logs' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ items: [LOG_ROW] }));
    }
    if (url.pathname === '/api/v1/neuroshilling/campaigns') {
      if (request.method === 'POST') {
        const created = { ...CAMPAIGN, campaign_id: 'c9', name: 'Новая' };
        listed.push(created);
        return Promise.resolve(jsonResponse(created));
      }
      return Promise.resolve(jsonResponse({ campaigns: listed }));
    }
    if (url.pathname.endsWith('/start') || url.pathname.endsWith('/stop')) {
      return Promise.resolve(jsonResponse({ status: 'running', sent: 0, total: 2 }));
    }
    if (url.pathname.endsWith('/board')) {
      return Promise.resolve(jsonResponse(board));
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
    // The PUT echo is the campaign under test, not the module-level default: the page
    // adopts this answer, so a fixed echo would hide what a save carries back.
    return Promise.resolve(jsonResponse(board.campaign ?? CAMPAIGN));
  });
}

export function callsTo(pathname: string, method: string): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => new URL(request.url).pathname === pathname && request.method === method);
}

export function emitLogFrame(): void {
  const stream = (
    globalThis.EventSource as unknown as { last(): { emit(d: unknown): void } }
  ).last();
  stream.emit({ id: 1, ts: 'now', level: 'info', event: 'neuroshilling_started' });
}

export async function waitForRefetch(before: number): Promise<void> {
  await waitFor(
    () => {
      expect(callsTo('/api/v1/neuroshilling/campaigns', 'GET').length).toBeGreaterThan(before);
    },
    { timeout: 3000 },
  );
}
