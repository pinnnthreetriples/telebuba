import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { CommentModeToggle } from './CommentModeToggle';
import { jsonResponse, renderWithClient } from './NeurocommentPage.testHelpers';

const SETTINGS = {
  max_comments_per_hour: 10,
  max_comments_per_channel_per_day: 3,
  reply_delay_min_seconds: 3,
  reply_delay_max_seconds: 10,
  min_trust_score: 0,
  comment_mode: 'first',
  reply_wait_minutes: 10,
  updated_at: 'now',
};

const FIRST = 'Пишем первыми';
const REPLY = 'Отвечаем в комментариях';
const WAIT = 'Ожидание живого комментария';

// `put` decides what the write returns: a resolved response, or a promise the test never
// settles (the in-flight case); `wait` is the stored reply-wait the read reports.
function routeSettings(
  mode: string,
  put: () => Promise<Response> = () => Promise.resolve(jsonResponse(SETTINGS)),
  wait: number = SETTINGS.reply_wait_minutes,
) {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (request.method === 'PUT') return put();
    return Promise.resolve(
      jsonResponse({ ...SETTINGS, comment_mode: mode, reply_wait_minutes: wait }),
    );
  });
}

// The field only exists in reply mode, so every wait test waits for the read to land first.
async function waitField(mode = 'reply', wait?: number): Promise<HTMLElement> {
  routeSettings(mode, undefined, wait);
  renderWithClient(<CommentModeToggle />);
  const field = await screen.findByRole('spinbutton', { name: WAIT });
  await waitFor(() => {
    expect(field).toBeEnabled();
  });
  return field;
}

function puts(): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => request.method === 'PUT');
}

async function putBody(): Promise<Record<string, unknown>> {
  return (await puts()[0]!.clone().json()) as Record<string, unknown>;
}

test('both positions render, and the backend value is the pressed one', async () => {
  routeSettings('reply');
  renderWithClient(<CommentModeToggle />);

  expect(screen.getByRole('button', { name: FIRST })).toBeInTheDocument();
  // Pressed only once the read lands — before that the control shows the backend's own
  // fallback ('first'), never a third "nothing chosen" state.
  await waitFor(() => {
    expect(screen.getByRole('button', { name: REPLY })).toHaveAttribute('aria-pressed', 'true');
  });
  expect(screen.getByRole('button', { name: FIRST })).toHaveAttribute('aria-pressed', 'false');
});

test('picking the other position sends the mode with the stored limits intact', async () => {
  routeSettings('first');
  renderWithClient(<CommentModeToggle />);
  await waitFor(() => {
    expect(screen.getByRole('button', { name: REPLY })).toBeEnabled();
  });

  await userEvent.click(screen.getByRole('button', { name: REPLY }));

  await waitFor(() => {
    expect(puts()).toHaveLength(1);
  });
  // The route replaces the limits wholesale, so the flip has to carry them back unchanged.
  expect(await putBody()).toEqual({
    max_comments_per_hour: 10,
    max_comments_per_channel_per_day: 3,
    reply_delay_min_seconds: 3,
    reply_delay_max_seconds: 10,
    min_trust_score: 0,
    comment_mode: 'reply',
  });
});

test('re-clicking while the write is in flight sends nothing more', async () => {
  // A write that never settles: `mode` therefore still reads 'first', so without the
  // in-flight guard the second click would send the very same body again.
  routeSettings('first', () => new Promise<Response>(() => undefined));
  renderWithClient(<CommentModeToggle />);
  await waitFor(() => {
    expect(screen.getByRole('button', { name: REPLY })).toBeEnabled();
  });

  const reply = screen.getByRole('button', { name: REPLY });
  await userEvent.click(reply);
  await waitFor(() => {
    expect(reply).toBeDisabled();
  });
  await userEvent.click(reply);

  expect(puts()).toHaveLength(1);
});

test('the wait field carries the stored minutes, and only in reply mode', async () => {
  const field = await waitField('reply', 45);

  expect(field).toHaveValue(45);
});

test('no wait field while the fleet comments first — the wait does not apply there', async () => {
  routeSettings('first');
  renderWithClient(<CommentModeToggle />);
  await waitFor(() => {
    expect(screen.getByRole('button', { name: REPLY })).toBeEnabled();
  });

  expect(screen.queryByRole('spinbutton', { name: WAIT })).not.toBeInTheDocument();
});

test('a new wait value goes out with the stored limits intact', async () => {
  const field = await waitField();

  await userEvent.clear(field);
  await userEvent.type(field, '45');
  await userEvent.tab(); // blur is the commit, so nothing is sent mid-typing

  await waitFor(() => {
    expect(puts()).toHaveLength(1);
  });
  // No `comment_mode`: omitted means "leave as stored", and the limits still replace
  // wholesale, so they have to travel back untouched.
  expect(await putBody()).toEqual({
    max_comments_per_hour: 10,
    max_comments_per_channel_per_day: 3,
    reply_delay_min_seconds: 3,
    reply_delay_max_seconds: 10,
    min_trust_score: 0,
    reply_wait_minutes: 45,
  });
});

test('a wait outside the schema bounds is never sent, and the stored value returns', async () => {
  const field = await waitField();

  for (const rejected of ['500', '0']) {
    await userEvent.clear(field);
    await userEvent.type(field, rejected);
    await userEvent.tab();

    expect(puts()).toHaveLength(0);
    expect(field).toHaveValue(10); // ge=1/le=120 would only have earned a 422
  }
});

test('the pressed position stays where the backend put it when the write fails', async () => {
  routeSettings('first', () => Promise.resolve(new Response('nope', { status: 500 })));
  renderWithClient(<CommentModeToggle />);
  await waitFor(() => {
    expect(screen.getByRole('button', { name: REPLY })).toBeEnabled();
  });

  await userEvent.click(screen.getByRole('button', { name: REPLY }));

  await waitFor(() => {
    expect(screen.getByRole('button', { name: FIRST })).toHaveAttribute('aria-pressed', 'true');
  });
});

test('each mode explains itself with a plain-language line and a worked example', async () => {
  routeSettings('first');
  renderWithClient(<CommentModeToggle />);
  await waitFor(() => {
    expect(screen.getByRole('button', { name: REPLY })).toBeEnabled();
  });

  // Both bubbles are in the DOM at all times — CSS reveals them on hover/focus, so this
  // asserts the content, and the `title` below is what a device that cannot hover gets.
  const hints = screen.getAllByRole('tooltip');
  expect(hints).toHaveLength(2);
  expect(hints[0]).toHaveTextContent('всегда первые в ветке');
  expect(hints[0]).toHaveTextContent('Пост в 12:00 → наш комментарий в 12:00.');
  expect(hints[1]).toHaveTextContent('отвечаем одному из них');
  // The example teaches the 2nd-4th rule rather than restating the label.
  expect(hints[1]).toHaveTextContent('в 12:07 отвечаем второму');
});

test('the hint is also reachable without hovering', async () => {
  routeSettings('first');
  renderWithClient(<CommentModeToggle />);
  const button = await screen.findByRole('button', { name: FIRST });

  // A touch device and a screen reader never hover; the native tooltip carries both halves.
  expect(button.title).toContain('всегда первые в ветке');
  expect(button.title).toContain('Пост в 12:00');
});
