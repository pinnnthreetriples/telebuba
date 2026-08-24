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

test('the hourly cap cannot be driven to 0, the value the API refuses', async () => {
  // Zero reads as "no cap" on the other two rows, so the box has to stop the operator
  // rather than let a full-replace save 422 and take the other two edits down with it.
  renderWithClient(<AccountLimitsModal accountId="a1" name="Polina" onClose={vi.fn()} />);
  await screen.findByText('20 / 20');
  const hourly = screen.getByLabelText('Комментарии в час');

  await userEvent.clear(hourly);
  await userEvent.type(hourly, '0');
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(async () => {
    expect((await putBody()).max_comments_per_hour).toBe(1);
  });
});

test('a decimal or an absurd cap is clamped before it can reach the API', async () => {
  renderWithClient(<AccountLimitsModal accountId="a1" name="Polina" onClose={vi.fn()} />);
  await screen.findByText('20 / 20');
  const joins = screen.getByLabelText('Вступления в каналы');

  // Truncated, never handed to the API as a float — Pydantic's int would refuse it.
  await userEvent.type(joins, '1.5');
  expect(joins).toHaveValue(1);

  await userEvent.clear(joins);
  await userEvent.type(joins, '99999999999999999999');
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(async () => {
    expect((await putBody()).max_joins_per_day).toBe(10000);
  });
});

test('an untouched cap stored above the ceiling is clamped, not echoed into a 422', async () => {
  // The row predates the ceiling, and the operator is editing a different cap. A blind
  // echo would 422 the whole replace and take the edit they DID make with it.
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(
      new Response(
        JSON.stringify({
          ...VIEW,
          joins: { ...VIEW.joins, limit: 50000, overridden: true },
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    ),
  );
  renderWithClient(<AccountLimitsModal accountId="a1" name="Polina" onClose={vi.fn()} />);
  await screen.findByText('20 / 50000');

  await userEvent.clear(screen.getByLabelText('Комментарии в час'));
  await userEvent.type(screen.getByLabelText('Комментарии в час'), '5');
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(async () => {
    const body = await putBody();
    expect(body.max_joins_per_day).toBe(10000);
    expect(body.max_comments_per_hour).toBe(5);
  });
});
