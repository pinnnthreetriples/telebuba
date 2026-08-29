import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { DialogueFeedMessage } from '@/shared/api';

import { DialogueFeed, DialogueTranscript } from './DialogueFeed';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function message(overrides: Partial<DialogueFeedMessage> = {}): DialogueFeedMessage {
  return {
    from_account: 'a1',
    from_label: '+79051184490',
    to_account: 'a2',
    to_label: '+79161234567',
    text: 'Привет!',
    created_at: '2026-07-01T14:00:00Z',
    ...overrides,
  };
}

// Liveness is the age of the newest line against the real clock, so fixtures
// that mean "just now" have to be built from it rather than frozen in a literal.
function secondsAgo(seconds: number): string {
  return new Date(Date.now() - seconds * 1000).toISOString();
}

const DAYS = 24 * 60 * 60;

test('renders each fed message with its from→to labels and text', () => {
  render(
    <DialogueTranscript
      messages={[
        message({ text: 'Привет!', from_label: '+79051184490', to_label: '+79161234567' }),
      ]}
    />,
  );
  expect(screen.getByText('+79051184490')).toBeInTheDocument();
  expect(screen.getByText('+79161234567')).toBeInTheDocument();
  expect(screen.getByText('Привет!')).toBeInTheDocument();
});

test('shows the Telegram name instead of the phone when the account has one', () => {
  render(
    <DialogueTranscript
      messages={[
        message({
          from_label: '527717224137',
          from_first_name: 'Polina',
          to_label: '528671176536',
          to_first_name: 'Alisa',
          to_last_name: 'K',
        }),
      ]}
    />,
  );
  // Order-aware on purpose: getByText alone is blind to the two sides being
  // swapped, so it would stay green on a from/to mix-up. Assert the sender is
  // the FIRST name in the row, ahead of the recipient.
  const names = screen.getAllByText(/Polina|Alisa K/).map((el) => el.textContent);
  expect(names).toEqual(['Polina', 'Alisa K']);
  expect(screen.queryByText('527717224137')).not.toBeInTheDocument();
  expect(screen.queryByText('528671176536')).not.toBeInTheDocument();
});

test('falls back per side: named side shows the name, unnamed side keeps its label', () => {
  render(
    <DialogueTranscript
      messages={[
        message({
          from_label: '527717224137',
          from_first_name: 'Polina',
          to_label: 'ghost-account',
          to_first_name: null,
          to_last_name: null,
        }),
      ]}
    />,
  );
  expect(screen.getByText('Polina')).toBeInTheDocument();
  expect(screen.getByText('ghost-account')).toBeInTheDocument();
});

test('shows the empty state when there are no messages', () => {
  render(<DialogueTranscript messages={[]} />);
  expect(screen.getByText('Пока нет переписки')).toBeInTheDocument();
});

test('newly-arrived messages animate in; already-seen ones do not re-animate', () => {
  const first = message({ text: 'first', created_at: '2026-07-01T14:00:00Z' });
  const { rerender } = render(<DialogueTranscript messages={[first]} />);
  // On first render the message is new → it carries the enter-animation class.
  expect(screen.getByText('first').closest('.tb-swapin')).not.toBeNull();

  // A newer message arrives (API is newest-first, so it is prepended).
  const second = message({ text: 'second', created_at: '2026-07-01T14:05:00Z' });
  rerender(<DialogueTranscript messages={[second, first]} />);
  // Only the genuinely-new message animates; the previously-seen one is static.
  expect(screen.getByText('second').closest('.tb-swapin')).not.toBeNull();
  expect(screen.getByText('first').closest('.tb-swapin')).toBeNull();
});

test('shows the typing indicator while the newest message is fresh', () => {
  render(<DialogueTranscript messages={[message({ created_at: secondsAgo(10) })]} />);
  expect(screen.getByText('печатает…')).toBeInTheDocument();
});

test('hides the typing indicator once the newest message has gone stale', () => {
  render(<DialogueTranscript messages={[message({ created_at: secondsAgo(5 * DAYS) })]} />);
  expect(screen.queryByText('печатает…')).not.toBeInTheDocument();
});

test('a fresh feed pulses the live dot and says the accounts are typing', async () => {
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse({ messages: [message({ text: 'fresh', created_at: secondsAgo(10) })] }),
  );
  const { container } = renderWithClient(<DialogueFeed />);

  await screen.findByText('fresh');
  expect(container.querySelector('.tb-livedot')).not.toBeNull();
  expect(screen.getByText('печатает…')).toBeInTheDocument();
});

// The regression itself: zero accounts warming, newest line days old, and the
// card still advertised a pulsing "live" dot and «печатает…».
test('a feed idle for days shows a static muted dot and no typing indicator', async () => {
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse({ messages: [message({ text: 'stale', created_at: secondsAgo(5 * DAYS) })] }),
  );
  const { container } = renderWithClient(<DialogueFeed />);

  await screen.findByText('stale');
  expect(container.querySelector('.tb-livedot')).toBeNull();
  expect(container.querySelector('.bg-content-subtle')).not.toBeNull();
  expect(screen.queryByText('печатает…')).not.toBeInTheDocument();
});

// Liveness has to decay on its own, with no new data to trigger it. The failure
// mode is subtle: the 4s poll keeps returning an identical payload, React
// Query's structural sharing hands back the previous `data` reference, and the
// tracked-props optimisation only notifies on properties actually read — so the
// component never re-renders, `Date.now()` is never re-evaluated, and one real
// exchange leaves the card advertising itself as live forever. Nothing changes
// in this test except the clock.
test('a feed that goes quiet while the page stays open stops claiming to be live', async () => {
  vi.useFakeTimers();
  try {
    const fresh = message({ text: 'fresh', created_at: secondsAgo(1) });
    // A fresh Response per call: a body can only be read once and the poll fires
    // dozens of times here.
    vi.mocked(fetch).mockImplementation(() => Promise.resolve(jsonResponse({ messages: [fresh] })));
    const { container } = renderWithClient(<DialogueFeed />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByText('fresh')).toBeInTheDocument();
    expect(container.querySelector('.tb-livedot')).not.toBeNull();
    expect(screen.getByText('печатает…')).toBeInTheDocument();

    // The exchange ends. No new message ever arrives — only time passes, well
    // past the liveness window.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(125_000);
    });

    expect(container.querySelector('.tb-livedot')).toBeNull();
    expect(container.querySelector('.bg-content-subtle')).not.toBeNull();
    expect(screen.queryByText('печатает…')).not.toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});

test('the badge counts the loaded messages while the page is not full', async () => {
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse({
      messages: [message(), message({ created_at: '2026-07-01T13:00:00Z' })],
    }),
  );
  renderWithClient(<DialogueFeed />);

  expect(await screen.findByText('2')).toBeInTheDocument();
});

// A full page is a page, not a total: history beyond the 30 requested rows is
// real, so the badge must not read as a frozen "30".
test('the badge reads 30+ once the requested page is saturated', async () => {
  const page = Array.from({ length: 30 }, (_, index) =>
    message({
      text: `m${String(index)}`,
      created_at: `2026-07-01T14:00:${String(index).padStart(2, '0')}Z`,
    }),
  );
  vi.mocked(fetch).mockResolvedValue(jsonResponse({ messages: page }));
  renderWithClient(<DialogueFeed />);

  expect(await screen.findByText('30+')).toBeInTheDocument();
  expect(screen.queryByText('30')).not.toBeInTheDocument();
});

test('polls the dialogue feed with the limit and renders the fetched messages', async () => {
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse({ messages: [message({ text: 'ping', to_label: '+15550000000' })] }),
  );
  renderWithClient(<DialogueFeed />);

  await waitFor(() => {
    expect(screen.getByText('ping')).toBeInTheDocument();
  });
  expect(screen.getByText('+15550000000')).toBeInTheDocument();
  const requested = vi
    .mocked(fetch)
    .mock.calls.some(([input]) => (input as Request).url.includes('/api/v1/warming/dialogues'));
  expect(requested).toBe(true);
  const withLimit = vi
    .mocked(fetch)
    .mock.calls.some(([input]) => (input as Request).url.includes('limit=30'));
  expect(withLimit).toBe(true);
});
