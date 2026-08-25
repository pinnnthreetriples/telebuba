import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { ListenerEditModal } from './ListenerEditModal';

const OPTIONS = [
  { id: 'a1', name: 'Ivan Petrov' },
  { id: 'a2', name: 'Maria Sidorova' },
];

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

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

// `put` decides what the write returns; `mode`/`wait` are what the read reports as stored.
function routeSettings(
  mode = 'first',
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

function puts(): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => request.method === 'PUT');
}

async function putBody(): Promise<Record<string, unknown>> {
  return (await puts()[0]!.clone().json()) as Record<string, unknown>;
}

function renderModal(selected: string | null = null) {
  const onClose = vi.fn();
  const onSave = vi.fn();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ListenerEditModal options={OPTIONS} selected={selected} onClose={onClose} onSave={onSave} />
    </QueryClientProvider>,
  );
  return { onClose, onSave };
}

// The mode buttons are disabled until the stored settings land, because the PUT body is
// built from them — so every mode/wait test waits for that read first.
async function openWithSettings(mode = 'first', wait?: number) {
  routeSettings(mode, undefined, wait);
  const handles = renderModal();
  await waitFor(() => {
    expect(screen.getByRole('radio', { name: REPLY })).toBeEnabled();
  });
  return handles;
}

test('opens the dropdown, picks an option, saves with swap and closes', async () => {
  routeSettings();
  const { onClose, onSave } = renderModal();
  expect(screen.getByText('Аккаунт-слушатель')).toBeInTheDocument();

  // open the custom dropdown and pick the second option
  await userEvent.click(screen.getByText('Выберите аккаунт…'));
  await userEvent.click(screen.getByText('Maria Sidorova'));

  await userEvent.click(screen.getByText('Сохранить'));
  expect(onSave).toHaveBeenCalledWith('a2');
  expect(screen.getByText('Сохранено')).toBeInTheDocument();
  await waitFor(() => {
    expect(onClose).toHaveBeenCalledTimes(1);
  });
  // The operator touched no setting, so the settings route was never written to.
  expect(puts()).toHaveLength(0);
});

// .tb-dd collapses VISUALLY only (max-height:0 + opacity:0), so the options are
// rendered whether the list is open or not — `inert` is the only thing keeping a
// keyboard operator out of a closed one. happy-dom honours inert for focus, which
// is exactly the property under test (it does not filter the a11y tree, so the
// option is still findable — that is the limitation, not the app's behaviour).
test('a closed dropdown takes no focus, an open one does', async () => {
  routeSettings();
  renderModal();

  const closed = screen.getByRole('option', { name: 'Maria Sidorova' });
  closed.focus();
  expect(closed).not.toHaveFocus();

  await userEvent.click(screen.getByText('Выберите аккаунт…'));
  const open = screen.getByRole('option', { name: 'Maria Sidorova' });
  open.focus();
  expect(open).toHaveFocus();
});

// The dropdown and the dialog both answer Escape, and Modal listens on `document`.
// One key must not both close the list and throw the whole modal away.
test('Escape closes the open dropdown without closing the modal', async () => {
  routeSettings();
  const { onClose } = renderModal();
  const trigger = screen.getByRole('button', {
    name: 'Аккаунт',
  });

  await userEvent.click(trigger);
  expect(trigger).toHaveAttribute('aria-expanded', 'true');

  await userEvent.keyboard('{Escape}');
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
  expect(onClose).not.toHaveBeenCalled();

  // With the list closed the same key belongs to the dialog again.
  await userEvent.keyboard('{Escape}');
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('cancel closes without saving', async () => {
  routeSettings();
  const { onClose, onSave } = renderModal('a1');
  await userEvent.click(screen.getByLabelText('Закрыть'));
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(onSave).not.toHaveBeenCalled();
});

// The account is a draft too, and always was: "Отмена" has to leave the page's listener
// alone even after the operator picked someone else.
test('an account picked and then cancelled is never applied', async () => {
  routeSettings();
  const { onClose, onSave } = renderModal();

  await userEvent.click(screen.getByText('Выберите аккаунт…'));
  await userEvent.click(screen.getByText('Maria Sidorova'));
  await userEvent.click(screen.getByText('Отмена'));

  expect(onSave).not.toHaveBeenCalled();
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('the stored mode is the checked one, and the stored wait is in the field', async () => {
  await openWithSettings('reply', 45);

  expect(screen.getByRole('radio', { name: REPLY })).toHaveAttribute('aria-checked', 'true');
  expect(screen.getByRole('radio', { name: FIRST })).toHaveAttribute('aria-checked', 'false');
  expect(screen.getByRole('spinbutton', { name: WAIT })).toHaveValue(45);
});

test('no wait field while the fleet comments first — the wait does not apply there', async () => {
  await openWithSettings('first');

  expect(screen.queryByRole('spinbutton', { name: WAIT })).not.toBeInTheDocument();
});

test('a mode picked and then saved goes out with the stored limits intact', async () => {
  await openWithSettings('first');

  await userEvent.click(screen.getByRole('radio', { name: REPLY }));
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    expect(puts()).toHaveLength(1);
  });
  // The route replaces the limits wholesale, so the flip has to carry them back unchanged;
  // an omitted `reply_wait_minutes` means "leave as stored".
  expect(await putBody()).toEqual({
    max_comments_per_hour: 10,
    max_comments_per_channel_per_day: 3,
    reply_delay_min_seconds: 3,
    reply_delay_max_seconds: 10,
    min_trust_score: 0,
    comment_mode: 'reply',
  });
});

// The reason the mode moved into this modal at all: the choice is a draft here, so the
// cancel button has to be telling the truth about it.
test('a mode picked and then cancelled sends nothing', async () => {
  const { onClose } = await openWithSettings('first');

  await userEvent.click(screen.getByRole('radio', { name: REPLY }));
  expect(screen.getByRole('radio', { name: REPLY })).toHaveAttribute('aria-checked', 'true');

  await userEvent.click(screen.getByText('Отмена'));

  expect(puts()).toHaveLength(0);
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('a mode returned to where it started costs no request', async () => {
  await openWithSettings('first');

  await userEvent.click(screen.getByRole('radio', { name: REPLY }));
  await userEvent.click(screen.getByRole('radio', { name: FIRST }));
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    expect(screen.getByText('Сохранено')).toBeInTheDocument();
  });
  expect(puts()).toHaveLength(0);
});

test('a new wait value goes out with the stored limits intact', async () => {
  await openWithSettings('reply');
  const field = screen.getByRole('spinbutton', { name: WAIT });

  await userEvent.clear(field);
  await userEvent.type(field, '45');
  await userEvent.tab(); // blur is the commit, so the draft never takes a mid-typing "4"
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    expect(puts()).toHaveLength(1);
  });
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
  await openWithSettings('reply');
  const field = screen.getByRole('spinbutton', { name: WAIT });

  for (const rejected of ['500', '0']) {
    await userEvent.clear(field);
    await userEvent.type(field, rejected);
    await userEvent.tab();

    expect(field).toHaveValue(10); // ge=1/le=120 would only have earned a 422
  }
  await userEvent.click(screen.getByText('Сохранить'));

  expect(puts()).toHaveLength(0);
});

test('a rejected settings write neither claims "Сохранено" nor closes', async () => {
  routeSettings('first', () => Promise.resolve(new Response('nope', { status: 500 })));
  const { onClose } = renderModal();
  await waitFor(() => {
    expect(screen.getByRole('radio', { name: REPLY })).toBeEnabled();
  });

  await userEvent.click(screen.getByRole('radio', { name: REPLY }));
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    expect(puts()).toHaveLength(1);
  });
  expect(screen.queryByText('Сохранено')).not.toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByText('Сохранить')).toBeEnabled();
});

test('each mode explains itself with a plain-language line and a worked example', async () => {
  await openWithSettings('first');

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
  await openWithSettings('first');

  // A touch device and a screen reader never hover; the native tooltip carries both halves.
  const button = screen.getByRole('radio', { name: FIRST });
  expect(button.title).toContain('всегда первые в ветке');
  expect(button.title).toContain('Пост в 12:00');
});
