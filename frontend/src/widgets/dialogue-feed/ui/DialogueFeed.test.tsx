import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StrictMode, type ReactElement } from 'react';
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

// ── The card ───────────────────────────────────────────────────────────────

test('lists a row per pair with both names, the reply count and the last line', async () => {
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse({
      messages: [
        message({ text: 'Спасибо, нашёл', created_at: '2026-07-01T14:03:00Z' }),
        message({ text: 'Скинь ту ссылку' }),
      ],
    }),
  );
  renderWithClient(<DialogueFeed />);

  expect(await screen.findByText('+79051184490')).toBeInTheDocument();
  expect(screen.getByText('+79161234567')).toBeInTheDocument();
  expect(screen.getByText('2')).toBeInTheDocument();
  expect(screen.getByText('Спасибо, нашёл')).toBeInTheDocument();
  expect(screen.getByText('1 пара')).toBeInTheDocument();
});

test('shows the Telegram name instead of the phone when the account has one', async () => {
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse({
      messages: [
        message({
          from_label: '527717224137',
          from_first_name: 'Polina',
          to_label: '528671176536',
          to_first_name: 'Alisa',
          to_last_name: 'K',
        }),
      ],
    }),
  );
  renderWithClient(<DialogueFeed />);

  expect(await screen.findByText('Polina')).toBeInTheDocument();
  expect(screen.getByText('Alisa K')).toBeInTheDocument();
  expect(screen.queryByText('527717224137')).not.toBeInTheDocument();
});

test('falls back per side: named side shows the name, unnamed side keeps its label', async () => {
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse({
      messages: [
        message({
          from_label: '527717224137',
          from_first_name: 'Polina',
          to_label: 'ghost-account',
          to_first_name: null,
          to_last_name: null,
        }),
      ],
    }),
  );
  renderWithClient(<DialogueFeed />);

  expect(await screen.findByText('Polina')).toBeInTheDocument();
  expect(screen.getByText('ghost-account')).toBeInTheDocument();
});

// The replies are behind a click now, which is the trade the narrow column
// bought: the row is a control, and it says so.
test('a pair opens its replies on click and closes them again', async () => {
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse({ messages: [message({ text: 'Скинь ту ссылку' })] }),
  );
  renderWithClient(<DialogueFeed />);

  const row = await screen.findByRole('button', { expanded: false });
  // Closed, the last line is the row's own subtitle; the transcript is not there.
  expect(screen.getAllByText('Скинь ту ссылку')).toHaveLength(1);

  await userEvent.click(row);
  expect(screen.getByRole('button', { expanded: true })).toBe(row);
  // Still exactly once: the row's subtitle is gone and the reply is a bubble.
  expect(screen.getAllByText('Скинь ту ссылку')).toHaveLength(1);

  await userEvent.click(row);
  expect(screen.getByRole('button', { expanded: false })).toBe(row);
});

test('opening a second pair closes the first', async () => {
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse({
      messages: [
        message({ from_account: 'b1', to_account: 'b2', created_at: '2026-07-01T15:00:00Z' }),
        message({ from_account: 'a1', to_account: 'a2' }),
      ],
    }),
  );
  renderWithClient(<DialogueFeed />);

  const rows = await screen.findAllByRole('button');
  await userEvent.click(rows[0]!);
  await userEvent.click(rows[1]!);

  expect(rows[0]).toHaveAttribute('aria-expanded', 'false');
  expect(rows[1]).toHaveAttribute('aria-expanded', 'true');
});

test('shows the empty state when there is no correspondence at all', async () => {
  vi.mocked(fetch).mockResolvedValue(jsonResponse({ messages: [] }));
  renderWithClient(<DialogueFeed />);

  expect(await screen.findByText('Пока нет переписки')).toBeInTheDocument();
});

test('a fresh feed pulses the live dot and says the accounts are typing', async () => {
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse({ messages: [message({ text: 'fresh', created_at: secondsAgo(10) })] }),
  );
  const { container } = renderWithClient(<DialogueFeed />);

  // On the CLOSED row: the typing pulse REPLACES the last line while the two
  // accounts are mid-exchange, so it is visible without opening anything.
  expect(await screen.findByText('печатает…')).toBeInTheDocument();
  expect(container.querySelector('.tb-livedot')).not.toBeNull();
  expect(screen.queryByText('fresh')).not.toBeInTheDocument();
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

// A day-old exchange used to read «14:03» and look like it happened minutes ago.
test('an old pair reads its age in days, not a time of day', async () => {
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse({ messages: [message({ created_at: secondsAgo(5 * DAYS) })] }),
  );
  renderWithClient(<DialogueFeed />);

  expect(await screen.findByText('5 дн.')).toBeInTheDocument();
});

test('yesterday reads as a word', async () => {
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse({ messages: [message({ created_at: secondsAgo(DAYS) })] }),
  );
  renderWithClient(<DialogueFeed />);

  expect(await screen.findByText('вчера')).toBeInTheDocument();
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
    // And the row goes back to showing what was said last.
    expect(screen.getByText('fresh')).toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
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

// Анимация прихода в СВЁРНУТОМ списке: пузыри уехали внутрь пары, поэтому
// вплывает строка. Проверяется под `StrictMode` — с записью во время рендера
// (первая версия) второй проход гасил признак, и в разработке не вплывало ничего.
test('a pair row animates in on arrival, stays put on an unchanged poll', async () => {
  vi.useFakeTimers();
  try {
    const first = message({ text: 'первое', created_at: '2026-07-01T14:00:00Z' });
    vi.mocked(fetch).mockImplementation(() => Promise.resolve(jsonResponse({ messages: [first] })));
    const { container } = render(
      <StrictMode>
        <QueryClientProvider
          client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
        >
          <DialogueFeed />
        </QueryClientProvider>
      </StrictMode>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    const row = () => container.querySelector('[aria-expanded]')?.parentElement;
    expect(row()?.className).toContain('tb-swapin');

    // Тот же ответ на следующих опросах: строка не должна вплывать заново.
    // Восемь секунд, а не четыре: обещание опроса завершается такт спустя после
    // самого таймера, поэтому первый `+4000` перерисовки ещё не даёт — класс на
    // экране остаётся с прихода. Анимация к этому времени давно отыграла (280мс),
    // так что видимого следа у этого такта нет.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000);
    });
    expect(row()?.className).not.toContain('tb-swapin');

    // Новая реплика в той же паре — вплывает.
    vi.mocked(fetch).mockImplementation(() =>
      Promise.resolve(
        jsonResponse({
          messages: [message({ text: 'второе', created_at: '2026-07-01T14:05:00Z' }), first],
        }),
      ),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000);
    });
    expect(row()?.className).toContain('tb-swapin');
  } finally {
    vi.useRealTimers();
  }
});

// ── One pair's transcript ──────────────────────────────────────────────────

test('the left account writes on the left, the other one on the right', () => {
  render(
    <DialogueTranscript
      leftAccount="a1"
      messages={[
        message({ from_account: 'a1', to_account: 'a2', text: 'слева' }),
        message({ from_account: 'a2', to_account: 'a1', text: 'справа' }),
      ]}
    />,
  );
  expect(screen.getByText('слева').className).toContain('self-start');
  expect(screen.getByText('справа').className).toContain('self-end');
});

test('newly-arrived messages animate in; already-seen ones do not re-animate', () => {
  const first = message({ text: 'first', created_at: '2026-07-01T14:00:00Z' });
  const { rerender } = render(<DialogueTranscript leftAccount="a1" messages={[first]} />);
  // On first render the message is new → it carries the enter-animation class.
  expect(screen.getByText('first').closest('.tb-swapin')).not.toBeNull();

  // A newer message arrives (the pair's transcript is oldest-first).
  const second = message({ text: 'second', created_at: '2026-07-01T14:05:00Z' });
  rerender(<DialogueTranscript leftAccount="a1" messages={[first, second]} />);
  // Only the genuinely-new message animates; the previously-seen one is static.
  expect(screen.getByText('second').closest('.tb-swapin')).not.toBeNull();
  expect(screen.getByText('first').closest('.tb-swapin')).toBeNull();
});

test('shows the typing indicator while the newest reply is fresh', () => {
  render(
    <DialogueTranscript leftAccount="a1" messages={[message({ created_at: secondsAgo(10) })]} />,
  );
  expect(screen.getByText('печатает…')).toBeInTheDocument();
});

test('hides the typing indicator once the newest reply has gone stale', () => {
  render(
    <DialogueTranscript
      leftAccount="a1"
      messages={[message({ created_at: secondsAgo(5 * DAYS) })]}
    />,
  );
  expect(screen.queryByText('печатает…')).not.toBeInTheDocument();
});
