import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '@/shared/i18n';

import { campaignsQueryOptions, neurocommentBoardQueryOptions } from '@/entities/campaign';
import type { DiscoveryBoard, DiscoveryCandidate } from '@/shared/api';

import { ChannelDiscoveryModal } from './ChannelDiscoveryModal';

type MockEventSourceCtor = {
  last: () => { emit: (payload: unknown) => void } | undefined;
};

function candidate(overrides: Partial<DiscoveryCandidate> = {}): DiscoveryCandidate {
  return {
    channel: 'alpha',
    title: 'Alpha',
    source: 'telegram_search',
    qualification: 'comments_on',
    ...overrides,
  };
}

function boardPayload(
  candidates: DiscoveryCandidate[],
  progress: Partial<DiscoveryBoard['progress']> = {},
): DiscoveryBoard {
  return {
    campaign_id: 'c1',
    progress: {
      phase: 'done',
      running: false,
      total: candidates.length,
      qualified: candidates.length,
      comments_on: candidates.length,
      last_error: null,
      ...progress,
    },
    candidates,
  };
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

type Routes = {
  board?: DiscoveryBoard;
  boards?: DiscoveryBoard[];
  startStatus?: string;
  hasTelemetrKey?: boolean;
  // One status per requested channel, defaulting to all-linked. The server reports
  // per-channel outcomes, so a spec has to be able to mix them.
  adoptStatuses?: string[];
  adoptFails?: boolean;
  boardFailures?: number;
  // Mutable so a spec can stall the board mid-test; a stalled fetch proves a row on
  // screen came from the cache rather than the server.
  hang?: { board: boolean };
};

function route(routes: Routes = {}) {
  const calls: { path: string; method: string; body: unknown }[] = [];
  const boards = routes.boards ?? (routes.board ? [routes.board] : [boardPayload([])]);
  let boardIndex = 0;
  let boardFailures = routes.boardFailures ?? 0;

  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const url = new URL(request.url);
    let body: unknown = null;
    if (request.method === 'POST') {
      body = JSON.parse(await request.clone().text());
    }
    calls.push({ path: url.pathname, method: request.method, body });

    if (url.pathname === '/api/v1/warming/settings') {
      return jsonResponse({
        inter_account_chat: false,
        reactions_enabled: true,
        gemini_model: 'gemini-2.5-flash',
        has_telemetr_key: routes.hasTelemetrKey ?? true,
        updated_at: 'now',
      });
    }
    if (url.pathname.endsWith('/discovery/search')) {
      return jsonResponse({ status: routes.startStatus ?? 'started' });
    }
    if (url.pathname.endsWith('/discovery/adopt')) {
      if (routes.adoptFails === true) {
        return new Response(JSON.stringify({ error: { code: 'internal', message: 'boom' } }), {
          status: 500,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      const requested = (body as { channels?: string[] } | null)?.channels ?? [];
      return jsonResponse({
        outcomes: requested.map((channel, index) => ({
          status: routes.adoptStatuses?.[index] ?? 'linked',
          channel,
        })),
      });
    }
    if (url.pathname.endsWith('/discovery')) {
      if (routes.hang?.board === true) return new Promise<Response>(() => undefined);
      if (boardFailures > 0) {
        boardFailures -= 1;
        return new Response(JSON.stringify({ error: { code: 'internal', message: 'boom' } }), {
          status: 500,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      const payload = boards[Math.min(boardIndex, boards.length - 1)];
      boardIndex += 1;
      return jsonResponse(payload);
    }
    return jsonResponse({});
  });

  return calls;
}

function renderModal(onClose = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ChannelDiscoveryModal campaignId="c1" campaignName="Promo" onClose={onClose} />
    </QueryClientProvider>,
  );
}

async function startSearch() {
  await userEvent.type(screen.getByPlaceholderText('крипта, трейдинг, новости'), 'crypto');
  await userEvent.click(screen.getByRole('button', { name: 'Найти' }));
}

beforeEach(() => {
  vi.useRealTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('ChannelDiscoveryModal', () => {
  it('opens on the form and switches to results after a started search', async () => {
    const calls = route({ board: boardPayload([candidate()]) });
    renderModal();

    expect(screen.getByRole('button', { name: 'Найти' })).toBeInTheDocument();
    await startSearch();

    await waitFor(() => {
      expect(screen.getByText('@alpha')).toBeInTheDocument();
    });
    const search = calls.find((call) => call.path.endsWith('/discovery/search'));
    expect(search?.body).toMatchObject({ keywords: ['crypto'], use_telemetr: false });
  });

  it('stays on the form and explains a refusal', async () => {
    route({ startStatus: 'daily_limit_reached' });
    renderModal();

    await startSearch();

    await waitFor(() => {
      expect(screen.getByText('Суточный лимит поисков исчерпан.')).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: 'Найти' })).toBeInTheDocument();
  });

  it('disables the Telemetr toggle when no key is configured', async () => {
    route({ hasTelemetrKey: false });
    renderModal();

    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: /Telemetr\.io/ })).toBeDisabled();
    });
  });

  it('refetches the board on a discovery SSE frame', async () => {
    const calls = route({
      boards: [
        boardPayload([candidate({ qualification: 'pending' })], {
          phase: 'qualifying',
          running: false,
          qualified: 0,
        }),
        boardPayload([candidate({ qualification: 'comments_on' })]),
      ],
    });
    renderModal();
    await startSearch();
    // running:false keeps the poll off, so only the SSE frame can refresh these rows.
    await waitFor(() => {
      expect(screen.getByText('не проверено')).toBeInTheDocument();
    });

    const source = (globalThis.EventSource as unknown as MockEventSourceCtor).last();
    source?.emit({ event: 'neurocomment_discovery_progress' });

    await waitFor(() => {
      expect(screen.getByLabelText('Комментарии включены')).toBeInTheDocument();
    });
    const boardCalls = calls.filter(
      (call) => call.path.endsWith('/discovery') && call.method === 'GET',
    );
    expect(boardCalls.length).toBeGreaterThan(1);
  });

  it('ignores an unrelated SSE frame', async () => {
    const calls = route({ board: boardPayload([candidate()]) });
    renderModal();
    await startSearch();
    await waitFor(() => {
      expect(screen.getByText('@alpha')).toBeInTheDocument();
    });
    const before = calls.filter(
      (call) => call.path.endsWith('/discovery') && call.method === 'GET',
    ).length;

    const source = (globalThis.EventSource as unknown as MockEventSourceCtor).last();
    source?.emit({ event: 'warming_cycle_done' });

    await new Promise((resolve) => {
      setTimeout(resolve, 600);
    });
    const after = calls.filter(
      (call) => call.path.endsWith('/discovery') && call.method === 'GET',
    ).length;
    expect(after).toBe(before);
  });

  it('counts the selection in the footer and adopts exactly those channels', async () => {
    const calls = route({
      board: boardPayload([
        candidate({ channel: 'good' }),
        candidate({ channel: 'alsogood' }),
        candidate({ channel: 'closed', qualification: 'comments_off' }),
      ]),
    });
    const onClose = vi.fn();
    renderModal(onClose);
    await startSearch();
    await waitFor(() => {
      expect(screen.getByText('@good')).toBeInTheDocument();
    });

    expect(screen.getByRole('button', { name: /Добавить выбранные \(0\)/ })).toBeDisabled();

    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать канал good' }));
    expect(screen.getByRole('button', { name: /Добавить выбранные \(1\)/ })).toBeEnabled();

    await userEvent.click(screen.getByRole('button', { name: /Добавить выбранные \(1\)/ }));

    await waitFor(() => {
      const adopt = calls.find((call) => call.path.endsWith('/discovery/adopt'));
      expect(adopt?.body).toEqual({ channels: ['good'] });
    });
    await waitFor(
      () => {
        expect(onClose).toHaveBeenCalled();
      },
      { timeout: 2000 },
    );
  });

  it('returns to the form and clears the selection', async () => {
    route({ board: boardPayload([candidate({ channel: 'good' })]) });
    renderModal();
    await startSearch();
    await waitFor(() => {
      expect(screen.getByText('@good')).toBeInTheDocument();
    });
    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать канал good' }));

    await userEvent.click(screen.getByRole('button', { name: '← Изменить параметры' }));

    expect(screen.getByRole('button', { name: 'Найти' })).toBeInTheDocument();
    // Starting the next run does not touch the picks, so a tick surviving onto its rows
    // would mean the back button failed to drop it.
    await userEvent.click(screen.getByRole('button', { name: 'Найти' }));
    await waitFor(() => {
      expect(screen.getByText('@good')).toBeInTheDocument();
    });
    expect(screen.getByRole('checkbox', { name: 'Выбрать канал good' })).not.toBeChecked();
    expect(screen.getByRole('button', { name: /Добавить выбранные \(0\)/ })).toBeInTheDocument();
  });

  it('drops the finished run rows as soon as the next search starts', async () => {
    const hang = { board: false };
    route({ board: boardPayload([candidate({ channel: 'first' })]), hang });
    renderModal();
    await startSearch();
    await waitFor(() => {
      expect(screen.getByText('@first')).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole('button', { name: '← Изменить параметры' }));
    // Run #2's board never answers, so anything on screen could only come from run #1's
    // cached frame — which is 'done', hence adoptable and poll-stopping.
    hang.board = true;
    await userEvent.click(screen.getByRole('button', { name: 'Найти' }));

    await waitFor(() => {
      expect(screen.getByText('Ищем каналы…')).toBeInTheDocument();
    });
    expect(screen.queryByText('@first')).not.toBeInTheDocument();
  });

  it('tells a channel taken elsewhere apart from one that failed to link', async () => {
    // Opposite next actions: taken is final, failed is worth retrying — so one
    // combined "not added" count would be misleading.
    route({
      board: boardPayload([candidate({ channel: 'good' }), candidate({ channel: 'alsogood' })]),
      adoptStatuses: ['linked', 'failed'],
    });
    renderModal();
    await startSearch();
    await waitFor(() => {
      expect(screen.getByText('@good')).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать все подходящие' }));
    await userEvent.click(screen.getByRole('button', { name: /Добавить выбранные \(2\)/ }));

    await waitFor(() => {
      expect(screen.getByText(/Не удалось добавить: 1/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/уже заняты/)).not.toBeInTheDocument();
  });

  it('stays open and reports the refused part of a partial adopt', async () => {
    route({
      board: boardPayload([candidate({ channel: 'good' }), candidate({ channel: 'alsogood' })]),
      adoptStatuses: ['linked', 'already_assigned'],
    });
    const onClose = vi.fn();
    renderModal(onClose);
    await startSearch();
    await waitFor(() => {
      expect(screen.getByText('@good')).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать все подходящие' }));
    await userEvent.click(screen.getByRole('button', { name: /Добавить выбранные \(2\)/ }));

    await waitFor(() => {
      expect(screen.getByText(/Не добавлено: 1/)).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /Добавлено: 1/ })).toBeInTheDocument();
    // well past the auto-close delay
    await new Promise((resolve) => {
      setTimeout(resolve, 1000);
    });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('reports a failed adopt and keeps the button retryable', async () => {
    route({ board: boardPayload([candidate({ channel: 'good' })]), adoptFails: true });
    const onClose = vi.fn();
    renderModal(onClose);
    await startSearch();
    await waitFor(() => {
      expect(screen.getByText('@good')).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать канал good' }));
    await userEvent.click(screen.getByRole('button', { name: /Добавить выбранные \(1\)/ }));

    await waitFor(() => {
      expect(screen.getByText(/Не удалось добавить каналы/)).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /Добавить выбранные \(1\)/ })).toBeEnabled();
    await new Promise((resolve) => {
      setTimeout(resolve, 1000);
    });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('keeps focus inside the dialog when the form gives way to the results', async () => {
    route({ board: boardPayload([candidate({ channel: 'good' })]) });
    renderModal();
    await startSearch();
    await waitFor(() => {
      expect(screen.getByText('@good')).toBeInTheDocument();
    });

    // The focused "Найти" button unmounted; focus on <body> would let the next Tab
    // walk into the page behind the modal.
    expect(screen.getByRole('dialog').contains(document.activeElement)).toBe(true);
  });

  it('reports a no-op adopt as a warning and stays open', async () => {
    route({
      board: boardPayload([candidate({ channel: 'good' })]),
      adoptStatuses: ['already_assigned'],
    });
    const onClose = vi.fn();
    renderModal(onClose);
    await startSearch();
    await waitFor(() => {
      expect(screen.getByText('@good')).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать канал good' }));
    await userEvent.click(screen.getByRole('button', { name: /Добавить выбранные \(1\)/ }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Ничего не добавлено/ })).toBeInTheDocument();
    });
    // well past the auto-close delay
    await new Promise((resolve) => {
      setTimeout(resolve, 1000);
    });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('does not blame an all-failed adopt on the channels being taken', async () => {
    route({
      board: boardPayload([candidate({ channel: 'good' })]),
      adoptStatuses: ['failed'],
    });
    renderModal();
    await startSearch();
    await waitFor(() => {
      expect(screen.getByText('@good')).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать канал good' }));
    await userEvent.click(screen.getByRole('button', { name: /Добавить выбранные \(1\)/ }));

    await waitFor(() => {
      expect(screen.getByText(/Не удалось добавить: 1/)).toBeInTheDocument();
    });
    // Nobody took the channel — the write itself failed, so the footer must not
    // contradict the paragraph one line above it.
    expect(screen.getByRole('button', { name: /Ничего не добавлено/ })).toBeInTheDocument();
    expect(screen.queryByText(/уже заняты/)).not.toBeInTheDocument();
  });

  it('lets the operator retry an adopt whose links failed', async () => {
    const calls = route({
      board: boardPayload([candidate({ channel: 'good' })]),
      adoptStatuses: ['failed'],
    });
    renderModal();
    await startSearch();
    await waitFor(() => {
      expect(screen.getByText('@good')).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать канал good' }));
    await userEvent.click(screen.getByRole('button', { name: /Добавить выбранные \(1\)/ }));

    // The copy promises "try again", so the button has to stay usable: a failed link
    // is not a settled outcome the way a taken channel is.
    const retry = await screen.findByRole('button', { name: /Ничего не добавлено/ });
    expect(retry).toBeEnabled();
    await userEvent.click(retry);

    await waitFor(() => {
      expect(calls.filter((call) => call.path.endsWith('/discovery/adopt'))).toHaveLength(2);
    });
  });

  it('does not adopt twice while the modal is closing', async () => {
    const calls = route({ board: boardPayload([candidate({ channel: 'good' })]) });
    renderModal();
    await startSearch();
    await waitFor(() => {
      expect(screen.getByText('@good')).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать канал good' }));
    await userEvent.click(screen.getByRole('button', { name: /Добавить выбранные \(1\)/ }));

    const done = await screen.findByRole('button', { name: /Добавлено/ });
    expect(done).toBeDisabled();
    await userEvent.click(done);
    expect(calls.filter((call) => call.path.endsWith('/discovery/adopt'))).toHaveLength(1);
  });

  it('keeps polling after a failed board fetch', async () => {
    route({ boardFailures: 1, board: boardPayload([candidate({ channel: 'good' })]) });
    renderModal();
    await startSearch();

    // The run is in flight, so the honest copy is "retrying", not "nothing found".
    await waitFor(() => {
      expect(screen.getByText(/Не удалось получить результаты/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/Ничего не нашлось/)).not.toBeInTheDocument();

    // One errored frame must not switch the self-disabling poll off for good.
    await waitFor(
      () => {
        expect(screen.getByText('@good')).toBeInTheDocument();
      },
      { timeout: 8000 },
    );
  }, 15000);

  it('closing mid-adopt still invalidates the campaign caches', async () => {
    // Escape or a backdrop click unmounts the modal, and mutate()'s onSuccess
    // lives on the mutation OBSERVER, which goes with it: the channels were
    // linked server-side and all three invalidations were dropped, so the
    // neurocomment board and campaign list kept a campaign without them.
    let releaseAdopt!: (response: Response) => void;
    vi.mocked(fetch).mockImplementation((input) => {
      const url = new URL((input as Request).url);
      if (url.pathname === '/api/v1/warming/settings') {
        return Promise.resolve(
          jsonResponse({
            inter_account_chat: false,
            reactions_enabled: true,
            gemini_model: 'gemini-2.5-flash',
            has_telemetr_key: true,
            updated_at: 'now',
          }),
        );
      }
      if (url.pathname.endsWith('/discovery/search')) {
        return Promise.resolve(jsonResponse({ status: 'started' }));
      }
      if (url.pathname.endsWith('/discovery/adopt')) {
        return new Promise<Response>((resolve) => {
          releaseAdopt = resolve;
        });
      }
      if (url.pathname.endsWith('/discovery')) {
        return Promise.resolve(jsonResponse(boardPayload([candidate({ channel: 'good' })])));
      }
      return Promise.resolve(jsonResponse({}));
    });

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const boardKey = neurocommentBoardQueryOptions({ path: { campaign_id: 'c1' } }).queryKey;
    const campaignsKey = campaignsQueryOptions().queryKey;
    queryClient.setQueryData(boardKey, {
      campaign_id: 'c1',
      campaign_name: 'Promo',
      status: 'active',
      channels: [],
      accounts: [],
    });
    queryClient.setQueryData(campaignsKey, { campaigns: [] });
    const { unmount } = render(
      <QueryClientProvider client={queryClient}>
        <ChannelDiscoveryModal campaignId="c1" campaignName="Promo" onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    await startSearch();
    await waitFor(() => {
      expect(screen.getByText('@good')).toBeInTheDocument();
    });
    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать канал good' }));
    await userEvent.click(screen.getByRole('button', { name: /Добавить выбранные \(1\)/ }));

    unmount();
    releaseAdopt(jsonResponse({ outcomes: [{ status: 'linked', channel: 'good' }] }));

    await waitFor(() => {
      expect(queryClient.getQueryState(boardKey)?.isInvalidated).toBe(true);
    });
    expect(queryClient.getQueryState(campaignsKey)?.isInvalidated).toBe(true);
  });

  it('shows the empty state when nothing was found', async () => {
    route({ board: boardPayload([]) });
    renderModal();

    await startSearch();

    await waitFor(() => {
      expect(screen.getByText(/Ничего не нашлось/)).toBeInTheDocument();
    });
  });
});
