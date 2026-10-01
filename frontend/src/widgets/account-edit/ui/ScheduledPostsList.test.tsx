import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { toLocalInput } from '@/features/schedule-post';
import type { ScheduledPostRead } from '@/shared/api';
import { Modal } from '@/shared/ui';

import { jsonResponse, renderWithClient } from './ProfileModal.test-helpers';
import { ScheduledPostsList } from './ScheduledPostsList';

const HOUR = 3_600_000;

function post(overrides: Partial<ScheduledPostRead>): ScheduledPostRead {
  const at = new Date(Date.now() + 2 * HOUR).toISOString();
  return {
    post_id: 'p1',
    kind: 'photo',
    state: 'pending',
    run_at: at,
    next_attempt_at: at,
    finished_at: null,
    attempts: 0,
    error_code: null,
    filename: 'me.png',
    media_kind: 'image',
    media_count: 1,
    caption: null,
    privacy_preset: null,
    story_id: null,
    thumb_url: '/api/v1/accounts/acc-1/scheduled/p1/thumb',
    ...overrides,
  };
}

function serve(items: ScheduledPostRead[]) {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts/acc-1/scheduled' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ items, server_now: new Date().toISOString() }));
    }
    const target = items.find((item) => pathname.endsWith(`/scheduled/${item.post_id}`));
    return Promise.resolve(jsonResponse({ ...(target ?? items[0]), state: 'cancelled' }));
  });
}

function calls(method: string, fragment: string): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => request.method === method && request.url.includes(fragment));
}

test('only the posts of its own kind are listed', async () => {
  serve([post({ post_id: 'p1' }), post({ post_id: 's1', kind: 'story', filename: 'story.png' })]);
  renderWithClient(<ScheduledPostsList accountId="acc-1" kind="photo" />);

  expect(await screen.findByText('Запланировано')).toBeInTheDocument();
  expect(screen.getAllByRole('listitem')).toHaveLength(1);
  expect(screen.getByText('Ожидает')).toBeInTheDocument();
});

test('a kind with no posts renders nothing once the list HAS loaded', async () => {
  serve([post({ kind: 'story' })]);
  // The story twin reads the same query: once it shows, the data is in, so the
  // photo list's absence is a verdict rather than a still-loading blank.
  renderWithClient(
    <>
      <ScheduledPostsList accountId="acc-1" kind="photo" />
      <ScheduledPostsList accountId="acc-1" kind="story" />
    </>,
  );

  expect(await screen.findByText('Запланировано')).toBeInTheDocument();
  expect(screen.getAllByText('Запланировано')).toHaveLength(1);
  expect(screen.getAllByRole('listitem')).toHaveLength(1);
});

test('a live scheduled-post event for this account refetches the list', async () => {
  serve([post({})]);
  renderWithClient(<ScheduledPostsList accountId="acc-1" kind="photo" />);
  await screen.findByText('Ожидает');
  const before = calls('GET', '/scheduled').length;
  const stream = (
    globalThis.EventSource as unknown as { last(): { emit(data: unknown): void } | undefined }
  ).last();

  act(() => {
    stream?.emit({ id: 1, event: 'account_scheduled_post_published', account_id: 'other' });
    stream?.emit({ id: 2, event: 'account_scheduled_post_published', account_id: 'acc-1' });
  });

  await waitFor(() => {
    expect(calls('GET', '/scheduled').length).toBeGreaterThan(before);
  });
});

test('a failed post shows its translated reason and can still be moved', async () => {
  serve([post({ state: 'failed', error_code: 'premium_required' })]);
  renderWithClient(<ScheduledPostsList accountId="acc-1" kind="photo" />);

  expect(await screen.findByText('Не удалось')).toBeInTheDocument();
  expect(
    screen.getByText('Для этого действия нужен Telegram Premium на аккаунте'),
  ).toBeInTheDocument();
  expect(screen.getByText('Перенести')).toBeInTheDocument();
});

test('a post being published right now offers no actions', async () => {
  serve([post({ state: 'processing' })]);
  renderWithClient(<ScheduledPostsList accountId="acc-1" kind="photo" />);

  expect(await screen.findByText('Публикуется')).toBeInTheDocument();
  expect(screen.queryByText('Перенести')).not.toBeInTheDocument();
  expect(screen.queryByText('Отменить')).not.toBeInTheDocument();
});

test('cancel asks first, then deletes the post', async () => {
  serve([post({})]);
  renderWithClient(<ScheduledPostsList accountId="acc-1" kind="photo" />);

  await userEvent.click(await screen.findByText('Отменить'));
  const dialog = screen.getByRole('dialog');
  await userEvent.click(within(dialog).getByText('Отменить публикацию'));

  await waitFor(() => {
    expect(calls('DELETE', '/api/v1/accounts/acc-1/scheduled/p1')).toHaveLength(1);
  });
});

test('move sends the new time as an aware ISO instant', async () => {
  serve([post({})]);
  renderWithClient(<ScheduledPostsList accountId="acc-1" kind="photo" />);
  const target = Date.now() + 5 * HOUR;

  await userEvent.click(await screen.findByText('Перенести'));
  fireEvent.change(screen.getByLabelText('Новое время'), {
    target: { value: toLocalInput(target) },
  });
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    expect(calls('PATCH', '/api/v1/accounts/acc-1/scheduled/p1')).toHaveLength(1);
  });
  const body = (await calls('PATCH', '/scheduled/p1')[0]?.clone().json()) as { run_at: string };
  expect(Math.abs(new Date(body.run_at).getTime() - target)).toBeLessThan(60_000);
  expect(body.run_at.endsWith('Z')).toBe(true);
});

test('Escape closes only the editor, never the dialog around it', async () => {
  serve([post({})]);
  const onDialogClose = vi.fn();
  renderWithClient(
    <Modal onClose={onDialogClose} label="Профиль">
      <ScheduledPostsList accountId="acc-1" kind="photo" />
    </Modal>,
  );

  await userEvent.click(await screen.findByText('Перенести'));
  await userEvent.keyboard('{Escape}');

  expect(screen.queryByLabelText('Новое время')).not.toBeInTheDocument();
  expect(screen.getByText('Перенести')).toHaveFocus();
  expect(onDialogClose).not.toHaveBeenCalled();
});

test('Enter in the time field saves it', async () => {
  serve([post({})]);
  renderWithClient(<ScheduledPostsList accountId="acc-1" kind="photo" />);

  await userEvent.click(await screen.findByText('Перенести'));
  await userEvent.type(screen.getByLabelText('Новое время'), '{Enter}');

  await waitFor(() => {
    expect(calls('PATCH', '/scheduled/p1')).toHaveLength(1);
  });
});

test('a cancel that finds the post already going out just closes the dialog', async () => {
  serve([post({})]);
  const base = vi.mocked(fetch).getMockImplementation();
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (request.method === 'DELETE') {
      return Promise.resolve(
        jsonResponse({ error: { code: 'conflict', message: 'scheduled_not_reschedulable' } }, 409),
      );
    }
    return base ? base(input) : Promise.resolve(jsonResponse({}));
  });
  renderWithClient(<ScheduledPostsList accountId="acc-1" kind="photo" />);

  await userEvent.click(await screen.findByText('Отменить'));
  await userEvent.click(within(screen.getByRole('dialog')).getByText('Отменить публикацию'));

  await waitFor(() => {
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

test('Escape during a save keeps both the editor and the dialog open', async () => {
  serve([post({})]);
  const base = vi.mocked(fetch).getMockImplementation();
  let release: () => void = () => undefined;
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    if (request.method === 'PATCH') {
      await new Promise<void>((resolve) => {
        release = resolve;
      });
    }
    return base ? base(input) : jsonResponse({});
  });
  const onDialogClose = vi.fn();
  renderWithClient(
    <Modal onClose={onDialogClose} label="Профиль">
      <ScheduledPostsList accountId="acc-1" kind="photo" />
    </Modal>,
  );

  await userEvent.click(await screen.findByText('Перенести'));
  await userEvent.type(screen.getByLabelText('Новое время'), '{Enter}');
  await waitFor(() => {
    expect(calls('PATCH', '/scheduled/p1')).toHaveLength(1);
  });
  expect(screen.getByLabelText('Новое время')).toHaveFocus();
  await userEvent.keyboard('{Escape}');

  expect(onDialogClose).not.toHaveBeenCalled();
  expect(screen.getByLabelText('Новое время')).toBeInTheDocument();
  release();
});
