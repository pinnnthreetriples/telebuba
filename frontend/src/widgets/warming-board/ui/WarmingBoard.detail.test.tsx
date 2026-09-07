import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { LogEntry } from '@/shared/api';

import { WarmingBoard } from './WarmingBoard';

// The log-line detail cases for the warming extras. Split from WarmingBoard.test.tsx,
// which sits at the 700-line file cap; the helpers are the same three lines as there.
function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

async function renderLog(items: Partial<LogEntry>[]): Promise<void> {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === '/api/v1/logs') {
      return Promise.resolve(
        jsonResponse({
          items: items.map((item, i) => ({
            id: i + 1,
            created_at: `2026-09-07T12:0${String(i)}:00+00:00`,
            level: 'INFO',
            status: 'success',
            account_id: '79051184490',
            ...item,
          })),
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <WarmingBoard
        warming={[
          {
            account_id: '79051184490',
            label: '79051184490',
            state: 'active',
            health: 'ok',
            cycles_completed: 2,
            trust_score: 70,
          },
        ]}
        onStop={vi.fn()}
        onPromote={vi.fn()}
        busyIds={new Set<string>()}
      />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByText('Лог активности'));
}

test('a media row says how much was downloaded, in whole kilobytes', async () => {
  await renderLog([
    {
      event: 'warming_telegram_warm_consume_media',
      extra: { channel: '@news', kind: 'video', bytes: 262_144, status: 'ok' },
    },
  ]);
  await waitFor(() => {
    expect(screen.getByText('Просмотр медиа')).toBeInTheDocument();
  });
  expect(screen.getByText('· скачано 256 КБ')).toBeInTheDocument();
});

test('an extra that ran and found nothing to do shows the warm_skip reason', async () => {
  await renderLog([
    {
      event: 'warming_telegram_warm_consume_media',
      extra: { channel: '@news', kind: 'voice', warm_skip: 'post_gone', status: 'ok' },
    },
    {
      event: 'warming_telegram_warm_emoji_status',
      extra: { warm_skip: 'premium_required', status: 'ok' },
    },
  ]);
  await waitFor(() => {
    expect(screen.getByText('Эмодзи-статус')).toBeInTheDocument();
  });
  // No bytes on a skipped download, so the skip reason is what the row explains.
  expect(screen.getByText('· пост удалён')).toBeInTheDocument();
  // The gateway's stable code is reached through the same ladder as failures.
  expect(
    screen.getByText('· Для этого действия нужен Telegram Premium на аккаунте'),
  ).toBeInTheDocument();
  expect(screen.queryByText(/скачано/)).not.toBeInTheDocument();
});
