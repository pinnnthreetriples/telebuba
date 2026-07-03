import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
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
