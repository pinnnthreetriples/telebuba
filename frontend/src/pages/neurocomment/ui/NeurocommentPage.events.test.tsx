import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import {
  BOARD,
  CAMPAIGN,
  jsonResponse,
  lastEventSource,
  renderWithClient,
  routeApi,
} from './NeurocommentPage.testHelpers';
import { NeurocommentPage } from './NeurocommentPage';

test('refetches runtime/board on a live SSE event', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  const boardCalls = () =>
    vi.mocked(fetch).mock.calls.filter(([input]) => (input as Request).url.endsWith('/board'))
      .length;
  const before = boardCalls();
  act(() => {
    lastEventSource()?.emit({ id: 1, event: 'neurocomment_comment_posted' });
  });
  await waitFor(() => {
    expect(boardCalls()).toBeGreaterThan(before);
  });
});

test('the pipeline stats include the errors odometer', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  expect(screen.getByText('ошибок')).toBeInTheDocument();
});

const STAGE_NAMES = ['Слушатель', 'Новый пост', 'Фильтр', 'Генерация', 'Капча', 'Комментарий'];

// Which of the six labels is active is only expressed by its weight, so this probes the
// class. What narrows it to the rail is the stage NAMES, not a utility class: the labels
// moved into the dot cells and the only class left marking them is `md:block`, which any
// future card on this page could carry and quietly hijack the assertion. The `span`
// filter also drops the below-`md` single-stage line, which is a div.
function activeStageLabel(container: HTMLElement): string {
  const labels = [...container.querySelectorAll<HTMLElement>('span')].filter((el) =>
    STAGE_NAMES.includes(el.textContent?.trim() ?? ''),
  );
  return labels.find((el) => el.className.includes('font-semibold'))?.textContent ?? '';
}

// Anti-decorative regression, the same one WarmingBoard.test.tsx keeps for its rail:
// `activeCell` used to be pinned to «Фильтр» for as long as the engine ran, so the
// operator watched a pipeline that never moved. It now reads the activity log.
test('the pipeline rail names the stage the log reports, not a hardcoded one', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(BOARD));
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: true, active_channels: 1, listener_account_id: 'acc-1' }),
      );
    }
    if (url.pathname === '/api/v1/logs') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              id: 1,
              created_at: new Date().toISOString(),
              level: 'INFO',
              status: 'success',
              event: 'neurocomment_posted',
            },
          ],
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  const { container } = renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(activeStageLabel(container)).toBe('Комментарий');
  });
});

test('the neuro log localizes a known event code and falls back for an unknown one', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(BOARD));
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: false, active_channels: 0, listener_account_id: null }),
      );
    }
    if (url.pathname === '/api/v1/logs') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              id: 1,
              created_at: 'now',
              level: 'INFO',
              status: 'success',
              event: 'neurocomment_posted',
            },
            {
              id: 2,
              created_at: 'now',
              level: 'INFO',
              status: 'success',
              event: 'some_unmapped_code',
            },
          ],
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getByText('Комментарий опубликован')).toBeInTheDocument();
  });
  // Unmapped code renders verbatim.
  expect(screen.getByText('some_unmapped_code')).toBeInTheDocument();
});

test('the neuro log shows the domain-prefixed gateway rows the engine triggered', async () => {
  // The `neurocomment` prefix filter now reaches the gateway's own rows because the
  // gateway stamps the calling domain onto them (`neurocomment_telegram_*`) — the
  // actual comment post and its flood wait, previously visible only to warming's card.
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(BOARD));
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: false, active_channels: 0, listener_account_id: null }),
      );
    }
    if (url.pathname === '/api/v1/logs') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              id: 1,
              created_at: 'now',
              level: 'INFO',
              status: 'success',
              event: 'neurocomment_telegram_comment_on_post',
            },
            {
              id: 2,
              created_at: 'now',
              level: 'WARNING',
              status: 'warning',
              event: 'neurocomment_telegram_comment_on_post_slow_mode_wait',
              extra: { seconds: 30 },
            },
          ],
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getByText('Комментарий к посту')).toBeInTheDocument();
  });
  expect(screen.getByText('Комментарий к посту — медленный режим')).toBeInTheDocument();
});

// The Odometer rolls a 0–9 digit column into place, so a tile's value is readable only
// as its settled offset: value N sits at translateY(-N*1.1em).
function errorsTileOffset(): string {
  const cell = screen.getByText('ошибок').parentElement;
  return cell?.querySelector<HTMLElement>('[style*="translateY"]')?.style.transform ?? '';
}

test('the errors tile counts the service verdict once, while the gateway twin stays red in the list', async () => {
  // Four rows, one intended error. The gateway rows reach this feed now that they carry the
  // domain prefix, and `_generate.py` writes its own classified row for the same outcome —
  // so counting both would double it, and `neurocomment_post_access_lost` is deliberately
  // amber even though it comes from `status == "failed"`. Expected tile value: 1.
  //   neurocomment_post_failed                            red,   counted  ← the verdict
  //   neurocomment_telegram_comment_on_post_failed        red,   twin, not counted
  //   neurocomment_post_access_lost                       amber, not counted
  //   neurocomment_telegram_join_discussion_group_failed  red,   UNTWINNED, not counted
  // Without the `_telegram_` exclusion the tile would read 3.
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(BOARD));
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: false, active_channels: 0, listener_account_id: null }),
      );
    }
    if (url.pathname === '/api/v1/logs') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              id: 1,
              created_at: 'now',
              // WARNING, not ERROR: `_generate.py` writes every verdict row at WARNING.
              // The FAILURE suffix rule is what makes this count, not the log level.
              level: 'WARNING',
              status: 'warning',
              event: 'neurocomment_post_failed',
            },
            {
              id: 2,
              created_at: 'now',
              level: 'ERROR',
              status: 'error',
              event: 'neurocomment_telegram_comment_on_post_failed',
            },
            {
              id: 3,
              created_at: 'now',
              level: 'WARNING',
              status: 'warning',
              event: 'neurocomment_post_access_lost',
            },
            {
              id: 4,
              created_at: 'now',
              level: 'ERROR',
              status: 'error',
              event: 'neurocomment_telegram_join_discussion_group_failed',
            },
          ],
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(errorsTileOffset()).toBe('translateY(-1.10em)');
  });
  // The twin is excluded from the count but still reads as a transport error in the list…
  expect(screen.getByText('Комментарий к посту — ошибка')).toHaveClass('text-term-error');
  // …and so does the UNTWINNED gateway failure, for which this row is the only evidence
  // there is (`_classify.py` logs nothing on that branch, `challenge.py` logs nothing at all).
  expect(screen.getByText('Вступление в чат канала — ошибка')).toHaveClass('text-term-error');
  // The deliberately-amber service verdict keeps its own colour.
  expect(screen.getByText('Потерян доступ к чату канала')).toHaveClass('text-term-warning');
});

test('the clear-log trash confirms, then DELETEs only the neurocomment logs', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(BOARD));
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: false, active_channels: 0, listener_account_id: null }),
      );
    }
    if (url.pathname === '/api/v1/logs' && request.method === 'GET') {
      return Promise.resolve(
        jsonResponse({
          items: [{ id: 1, created_at: 'now', level: 'INFO', status: 'success', event: 'x' }],
          next_cursor: null,
        }),
      );
    }
    if (url.pathname === '/api/v1/logs' && request.method === 'DELETE') {
      return Promise.resolve(jsonResponse({ deleted: 1 }));
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getByLabelText('Очистить лог')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByLabelText('Очистить лог'));
  const confirm = await screen.findByText('Очистить');
  const wasDeleted = () =>
    vi.mocked(fetch).mock.calls.some(([input]) => {
      const request = input as Request;
      const url = new URL(request.url);
      return (
        request.method === 'DELETE' &&
        url.pathname === '/api/v1/logs' &&
        url.searchParams.get('event_prefix') === 'neurocomment'
      );
    });
  expect(wasDeleted()).toBe(false); // not until confirmed
  await userEvent.click(confirm);
  await waitFor(() => {
    expect(wasDeleted()).toBe(true);
  });
});

test('the SSE callback invalidates only this page keys, not the whole cache', async () => {
  routeApi();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = vi.spyOn(queryClient, 'invalidateQueries');
  render(<QueryClientProvider client={queryClient}>{<NeurocommentPage />}</QueryClientProvider>);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  spy.mockClear();
  act(() => {
    lastEventSource()?.emit({ id: 1, event: 'neurocomment_posted', status: 'success' });
  });
  await waitFor(() => {
    expect(spy).toHaveBeenCalled();
  });
  // Every SSE-driven invalidation is scoped by a predicate (not a bare call).
  expect(
    spy.mock.calls.every(([arg]) => typeof arg === 'object' && arg !== null && 'predicate' in arg),
  ).toBe(true);
});

test('the gateway by-request row is hidden, leaving only the translated service line', async () => {
  // One join request, two rows: `_join_by_request_result` in the gateway and
  // `_classify.py`'s twin. Only the twin is translated and only it carries the attempt
  // ratio, so the gateway row reached the operator as a raw event code saying nothing new.
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(BOARD));
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: false, active_channels: 0, listener_account_id: null }),
      );
    }
    if (url.pathname === '/api/v1/logs') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              id: 1,
              created_at: 'now',
              level: 'INFO',
              status: 'success',
              event: 'neurocomment_onboard_join_by_request',
              extra: { channel: '@news', reason: '1/2' },
            },
            {
              id: 2,
              created_at: 'now',
              level: 'INFO',
              status: 'success',
              event: 'neurocomment_telegram_join_discussion_group_by_request',
              extra: { channel: '@news' },
            },
          ],
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(
      screen.getByText('Пускают только по заявкам. Заявка на вступление отправлена'),
    ).toBeInTheDocument();
  });
  expect(
    screen.queryByText('neurocomment_telegram_join_discussion_group_by_request'),
  ).not.toBeInTheDocument();
});
