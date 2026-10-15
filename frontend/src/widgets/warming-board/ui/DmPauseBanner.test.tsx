import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { DmPauseBanner } from './DmPauseBanner';

const LIFT_PATH = '/api/v1/warming/dm-pause/lift';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function liftCalls(): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => new URL(request.url).pathname === LIFT_PATH);
}

function answerOk() {
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify({ gemini_model: 'm', updated_at: 'now' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  );
}

test('shows when DMs reopen and lifts the pause only after a confirmation', async () => {
  answerOk();
  renderWithClient(<DmPauseBanner pausedUntil="2026-10-15T14:30:00+00:00" />);

  expect(screen.getByText(/^ЛС на паузе до 15 окт/)).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Снять паузу' }));

  // The click only asks: DMs reopen for the whole fleet, so nothing is sent yet.
  const dialog = await screen.findByRole('dialog', { name: 'Снять паузу ЛС для всего парка?' });
  expect(liftCalls()).toHaveLength(0);
  await userEvent.click(within(dialog).getByRole('button', { name: 'Снять паузу' }));

  await waitFor(() => {
    expect(liftCalls()).toHaveLength(1);
  });
  expect(liftCalls()[0]?.method).toBe('POST');
  await waitFor(() => {
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

test('cancelling the confirmation keeps the pause', async () => {
  answerOk();
  renderWithClient(<DmPauseBanner pausedUntil="2026-10-15T14:30:00+00:00" />);

  await userEvent.click(screen.getByRole('button', { name: 'Снять паузу' }));
  const dialog = await screen.findByRole('dialog');
  await userEvent.click(within(dialog).getByRole('button', { name: 'Отмена' }));

  await waitFor(() => {
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
  expect(liftCalls()).toHaveLength(0);
});

test('renders nothing while DMs are not paused', () => {
  const { container } = renderWithClient(<DmPauseBanner pausedUntil={null} />);
  expect(container).toBeEmptyDOMElement();
});

test('renders nothing for an unreadable pause stamp', () => {
  const { container } = renderWithClient(<DmPauseBanner pausedUntil="nope" />);
  expect(container).toBeEmptyDOMElement();
});
