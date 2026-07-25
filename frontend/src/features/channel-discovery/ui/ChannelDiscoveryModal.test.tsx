import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '@/shared/i18n';

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
  adoptLinked?: number;
};

function route(routes: Routes = {}) {
  const calls: { path: string; method: string; body: unknown }[] = [];
  const boards = routes.boards ?? (routes.board ? [routes.board] : [boardPayload([])]);
  let boardIndex = 0;

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
      const linked = routes.adoptLinked ?? 1;
      return jsonResponse({
        outcomes: Array.from({ length: linked }, (_, index) => ({
          status: 'linked',
          channel: `chan_${index}`,
        })),
      });
    }
    if (url.pathname.endsWith('/discovery')) {
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
    await waitFor(() => {
      expect(screen.getByText('проверяется')).toBeInTheDocument();
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
    await userEvent.click(screen.getByRole('button', { name: 'Найти' }));
    await waitFor(() => {
      expect(screen.getByText('@good')).toBeInTheDocument();
    });
    expect(screen.getByRole('checkbox', { name: 'Выбрать канал good' })).not.toBeChecked();
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
