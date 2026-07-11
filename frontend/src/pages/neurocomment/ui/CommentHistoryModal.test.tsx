import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { CommentRecord, NeurocommentAccountCard } from '@/shared/api';

import { CommentHistoryModal } from './CommentHistoryModal';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

const ACCOUNTS: NeurocommentAccountCard[] = [
  {
    account_id: 'acc-1',
    label: 'Account One',
    health: 'ready',
    trust_score: 80,
    trust_band: 'high',
    comments_last_hour: 1,
    max_comments_per_hour: 10,
    comments_today: 2,
  },
];

function comment(post_id: number, text: string): CommentRecord {
  return {
    channel: '@chan',
    post_id,
    campaign_id: 'c1',
    account_id: 'acc-1',
    status: 'posted',
    comment_text: text,
    created_at: '2026-07-11T10:00:00+00:00',
    updated_at: '2026-07-11T10:00:00+00:00',
  };
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function routeComments(page1: unknown, page2?: unknown) {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    const body = url.searchParams.get('cursor') ? (page2 ?? page1) : page1;
    return Promise.resolve(jsonResponse(body));
  });
}

test('renders comment rows with the resolved account label', async () => {
  routeComments({ items: [comment(2, 'second'), comment(1, 'first')], next_cursor: null });
  renderWithClient(<CommentHistoryModal campaignId="c1" accounts={ACCOUNTS} onClose={vi.fn()} />);
  await waitFor(() => {
    expect(screen.getByText('second')).toBeInTheDocument();
  });
  expect(screen.getByText('first')).toBeInTheDocument();
  expect(screen.getAllByText('Account One')).toHaveLength(2);
});

test('shows the empty state', async () => {
  routeComments({ items: [], next_cursor: null });
  renderWithClient(<CommentHistoryModal campaignId="c1" accounts={ACCOUNTS} onClose={vi.fn()} />);
  await waitFor(() => {
    expect(screen.getByText('Опубликованных комментариев пока нет')).toBeInTheDocument();
  });
});

test('next advances the cursor stack and prev goes back', async () => {
  routeComments(
    { items: [comment(1, 'first')], next_cursor: '50' },
    { items: [comment(2, 'second')], next_cursor: null },
  );
  renderWithClient(<CommentHistoryModal campaignId="c1" accounts={ACCOUNTS} onClose={vi.fn()} />);
  await waitFor(() => {
    expect(screen.getByText('first')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Вперёд'));
  await waitFor(() => {
    expect(screen.getByText('second')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Назад'));
  await waitFor(() => {
    expect(screen.getByText('first')).toBeInTheDocument();
  });
});
