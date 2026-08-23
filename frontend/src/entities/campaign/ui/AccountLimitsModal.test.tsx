import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { beforeEach, expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { AccountLimitsModal } from './AccountLimitsModal';

const VIEW = {
  account_id: 'a1',
  joins: {
    limit: 20,
    used: 20,
    fleet_default: 20,
    overridden: false,
    resets_at: '2026-08-24T16:07:00+00:00',
  },
  comments_per_hour: { limit: 30, used: 1, fleet_default: 10, overridden: true, resets_at: null },
  comments_per_channel_per_day: {
    limit: 3,
    used: 2,
    fleet_default: 3,
    overridden: false,
    resets_at: null,
  },
  busiest_channel: '@lentachold',
};

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(ui, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
}

function jsonResponse(): Response {
  return new Response(JSON.stringify(VIEW), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

async function putBody(): Promise<Record<string, number | null>> {
  const call = vi.mocked(fetch).mock.calls.find(([input]) => (input as Request).method === 'PUT');
  return (await (call![0] as Request).json()) as Record<string, number | null>;
}

beforeEach(() => {
  vi.mocked(fetch).mockImplementation(() => Promise.resolve(jsonResponse()));
});

test('shows the spend, the reset moment and which cap is the account own', async () => {
  renderWithClient(<AccountLimitsModal accountId="a1" name="Polina" onClose={vi.fn()} />);

  expect(await screen.findByText('20 / 20')).toBeInTheDocument();
  // The per-channel cap is per pair, so the row names the channel it was measured on.
  expect(screen.getByText(/@lentachold/)).toBeInTheDocument();
  // An overridden cap shows its own number in the box; an untouched one leaves the box
  // empty so the fleet default shows through as the placeholder.
  expect(screen.getByLabelText('Комментарии в час')).toHaveValue(30);
  expect(screen.getByLabelText('Вступления в каналы')).toHaveValue(null);
});

test('saving sends every cap, and an untouched one stays null', async () => {
  const onClose = vi.fn();
  renderWithClient(<AccountLimitsModal accountId="a1" name="Polina" onClose={onClose} />);
  await screen.findByText('20 / 20');

  await userEvent.type(screen.getByLabelText('Вступления в каналы'), '30');
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    expect(onClose).toHaveBeenCalled();
  });
  expect(await putBody()).toEqual({
    max_joins_per_day: 30,
    // Carried back unchanged: the PUT is a full replace, so an omitted override would be
    // silently dropped rather than left alone.
    max_comments_per_hour: 30,
    max_comments_per_channel_per_day: null,
  });
});

test('"back to fleet" clears every override rather than zeroing it', async () => {
  // Zero is a real limit ("no cap"), so handing a cap back to the fleet has to send null.
  renderWithClient(<AccountLimitsModal accountId="a1" name="Polina" onClose={vi.fn()} />);
  await screen.findByText('20 / 20');

  await userEvent.click(screen.getByText('Вернуть общие'));
  expect(screen.getByLabelText('Комментарии в час')).toHaveValue(null);

  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(async () => {
    expect(await putBody()).toEqual({
      max_joins_per_day: null,
      max_comments_per_hour: null,
      max_comments_per_channel_per_day: null,
    });
  });
});
