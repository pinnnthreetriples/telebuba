import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { toLocalInput } from '@/features/schedule-post';
import type { AccountRead } from '@/shared/api';

import { AddStoryModal } from './AddStoryModal';
import { BulkEditModal } from './BulkEditModal';
import { jsonResponse, renderWithClient } from './ProfileModal.test-helpers';
import { SchedulePhotosModal } from './SchedulePhotosModal';

// The three ways into a timed publish: the photo scheduler, the story composer's
// "on schedule" mode and the bulk editor. Each must upload through the media store
// and then schedule by JSON, and none of them may publish anything right away.

const FLEET: AccountRead[] = ['acc-1', 'acc-2', 'acc-3'].map((account_id) => ({
  account_id,
  status: 'alive',
  phone: `+7999000000${account_id.slice(-1)}`,
  created_at: 'now',
  updated_at: 'now',
}));

type Scheduled = { accountId: string; body: Record<string, unknown> };

function route() {
  const log = { uploads: 0, photos: [] as Scheduled[], stories: [] as Scheduled[], now: 0 };
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    const scheduled = /^\/api\/v1\/accounts\/([^/]+)\/scheduled\/(photo|story)$/.exec(pathname);
    if (pathname === '/api/v1/accounts' && request.method === 'GET') {
      return jsonResponse({ items: FLEET, next_cursor: null });
    }
    if (pathname === '/api/v1/scheduled/media') {
      log.uploads += 1;
      const mediaId = `${log.uploads.toString(16).padStart(64, '0')}.png`;
      return jsonResponse({ media_id: mediaId, media_kind: 'image', size_bytes: 1 });
    }
    if (scheduled) {
      const entry = {
        accountId: scheduled[1] ?? '',
        body: (await request.clone().json()) as Record<string, unknown>,
      };
      (scheduled[2] === 'photo' ? log.photos : log.stories).push(entry);
      return jsonResponse({ post_id: 'p', state: 'pending' });
    }
    if (pathname.endsWith('/story') || pathname === '/api/v1/accounts/photo') log.now += 1;
    if (pathname.endsWith('/scheduled')) {
      return jsonResponse({ items: [], server_now: new Date().toISOString() });
    }
    return jsonResponse({});
  });
  return log;
}

function png(name: string): File {
  return new File(['x'], name, { type: 'image/png' });
}

function pick(files: File[]) {
  const input = document.body.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files } });
}

test('each scheduled photo is uploaded, then scheduled at its own spread-out time', async () => {
  const log = route();
  const onClose = vi.fn();
  renderWithClient(<SchedulePhotosModal accountId="acc-1" onClose={onClose} />);

  pick([png('a.png'), png('b.png')]);
  await userEvent.click(screen.getByText('Раскидать'));
  await userEvent.click(screen.getByRole('button', { name: 'Запланировать 2 фото' }));

  await waitFor(() => {
    expect(log.photos).toHaveLength(2);
  });
  expect(log.uploads).toBe(2);
  expect(log.now).toBe(0);
  const [first, second] = log.photos.map((entry) => new Date(String(entry.body.run_at)).getTime());
  expect((second ?? 0) - (first ?? 0)).toBe(60 * 60_000);
  expect(log.photos.map((entry) => entry.body.filename)).toEqual(['a.png', 'b.png']);
  await waitFor(
    () => {
      expect(onClose).toHaveBeenCalled();
    },
    { timeout: 2000 },
  );
});

test('a refused row stays for a retry and the dialog does not close', async () => {
  route();
  const base = vi.mocked(fetch).getMockImplementation();
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    if (request.url.endsWith('/scheduled/photo')) {
      return jsonResponse(
        { error: { code: 'bad_request', message: 'scheduled_pending_limit' } },
        400,
      );
    }
    return base ? base(input) : jsonResponse({});
  });
  const onClose = vi.fn();
  renderWithClient(<SchedulePhotosModal accountId="acc-1" onClose={onClose} />);

  pick([png('a.png')]);
  await userEvent.click(screen.getByRole('button', { name: 'Запланировать 1 фото' }));

  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Запланировать 1 фото' })).toBeEnabled();
  });
  expect(onClose).not.toHaveBeenCalled();
});

test('the story composer schedules instead of publishing when set to a time', async () => {
  const log = route();
  const onPosted = vi.fn();
  renderWithClient(<AddStoryModal accountId="acc-1" onClose={vi.fn()} onPosted={onPosted} />);

  await userEvent.click(screen.getByText('По расписанию'));
  await userEvent.click(screen.getByText('Публично'));
  pick([png('s.png')]);
  await userEvent.click(screen.getByRole('button', { name: 'Запланировать' }));

  await waitFor(() => {
    expect(log.stories).toHaveLength(1);
  });
  expect(log.now).toBe(0);
  expect(onPosted).not.toHaveBeenCalled();
  const body = log.stories[0]?.body ?? {};
  expect(body.media_ids).toEqual([`${'1'.padStart(64, '0')}.png`]);
  expect(body.privacy_preset).toBe('public');
  expect(await screen.findByText('Запланировано')).toBeInTheDocument();
});

test('a bulk photo run uploads once and schedules every account, in order, keyed', async () => {
  const log = route();
  const user = userEvent.setup();
  renderWithClient(<BulkEditModal account={FLEET[0] as AccountRead} onClose={vi.fn()} />);

  await user.click(screen.getByRole('button', { name: 'Добавить аккаунты' }));
  await user.click(await screen.findByRole('checkbox', { name: 'Выбрать все (3)' }));
  await user.click(screen.getByRole('button', { name: 'Добавить (3)' }));
  await user.click(screen.getByRole('tab', { name: 'Фото' }));
  pick([png('face.png')]);
  await user.click(screen.getByText('По расписанию'));
  await user.click(screen.getByRole('button', { name: 'Запланировать для 3 аккаунтов' }));

  await waitFor(() => {
    expect(log.photos).toHaveLength(3);
  });
  expect(log.uploads).toBe(1);
  expect(log.now).toBe(0);
  expect(log.photos.map((entry) => entry.accountId)).toEqual(['acc-1', 'acc-2', 'acc-3']);
  expect(new Set(log.photos.map((entry) => entry.body.batch_id)).size).toBe(1);
  expect(log.photos.map((entry) => entry.body.client_key)).toEqual(['row0', 'row1', 'row2']);
  const times = log.photos.map((entry) => new Date(String(entry.body.run_at)).getTime());
  expect([...times].sort((a, b) => a - b)).toEqual(times);
  expect(await screen.findAllByText(/^Запланировано на /)).toHaveLength(3);
});

test('a retry re-sends neither the stored file nor a new key, and a refused row can go', async () => {
  const log = route();
  const base = vi.mocked(fetch).getMockImplementation();
  let refusedKey: unknown = null;
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    if (request.url.endsWith('/scheduled/photo') && refusedKey === null) {
      refusedKey = ((await request.clone().json()) as { client_key: unknown }).client_key;
      return jsonResponse({ error: { code: 'unavailable', message: 'unavailable' } }, 503);
    }
    return base ? base(input) : jsonResponse({});
  });
  renderWithClient(<SchedulePhotosModal accountId="acc-1" onClose={vi.fn()} />);

  pick([png('a.png'), png('b.png')]);
  await userEvent.click(screen.getByRole('button', { name: 'Запланировать 2 фото' }));
  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Запланировать 1 фото' })).toBeEnabled();
  });
  expect(screen.getByRole('button', { name: 'Убрать a.png' })).toBeEnabled();
  await userEvent.click(screen.getByRole('button', { name: 'Запланировать 1 фото' }));

  await waitFor(() => {
    expect(log.photos).toHaveLength(2);
  });
  expect(log.uploads).toBe(2); // a.png was stored on the first try and reused
  const keys = log.photos.map((entry) => [entry.body.batch_id, entry.body.client_key]);
  expect(new Set(keys.map(([batch]) => batch)).size).toBe(1);
  expect(new Set(keys.map(([, key]) => key)).size).toBe(2);
  // The retried row carries the very key its refused first try did.
  const retried = log.photos.find((entry) => entry.body.filename === 'a.png');
  expect(refusedKey).not.toBeNull();
  expect(retried?.body.client_key).toBe(refusedKey);
});

test('a bulk plan whose last account lands past a year cannot start', async () => {
  route();
  const user = userEvent.setup();
  renderWithClient(<BulkEditModal account={FLEET[0] as AccountRead} onClose={vi.fn()} />);

  await user.click(screen.getByRole('button', { name: 'Добавить аккаунты' }));
  await user.click(await screen.findByRole('checkbox', { name: 'Выбрать все (3)' }));
  await user.click(screen.getByRole('button', { name: 'Добавить (3)' }));
  await user.click(screen.getByRole('tab', { name: 'Фото' }));
  pick([png('face.png')]);
  await user.click(screen.getByText('По расписанию'));
  fireEvent.change(screen.getByLabelText('Между аккаунтами, мин'), { target: { value: '1440' } });
  const base = Date.now() + 364 * 86_400_000;
  fireEvent.change(screen.getByLabelText('Первый аккаунт'), {
    target: { value: toLocalInput(base) },
  });

  expect(screen.getByRole('alert')).toHaveTextContent('позже чем через год');
  expect(screen.getByRole('button', { name: 'Запланировать для 3 аккаунтов' })).toBeDisabled();
});

test('while a bulk run uploads its files, the dialog cannot be left or changed', async () => {
  route();
  const base = vi.mocked(fetch).getMockImplementation();
  let release: () => void = () => undefined;
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    if (request.url.endsWith('/scheduled/media')) {
      await new Promise<void>((resolve) => {
        release = resolve;
      });
    }
    return base ? base(input) : jsonResponse({});
  });
  const user = userEvent.setup();
  renderWithClient(<BulkEditModal account={FLEET[0] as AccountRead} onClose={vi.fn()} />);
  await user.click(screen.getByRole('tab', { name: 'Фото' }));
  pick([png('face.png')]);
  await user.click(screen.getByText('По расписанию'));
  await user.click(screen.getByRole('button', { name: 'Запланировать для 1 аккаунта' }));

  expect(screen.getByRole('button', { name: 'Отмена' })).toBeDisabled();
  expect(screen.getByRole('tab', { name: 'Текст' })).toBeDisabled();
  release();
  expect(await screen.findByText(/^Запланировано на /)).toBeInTheDocument();
});

test('a file the server swept is uploaded again on the retry', async () => {
  const log = route();
  const base = vi.mocked(fetch).getMockImplementation();
  let swept = true;
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    if (request.url.endsWith('/scheduled/photo') && swept) {
      swept = false;
      return jsonResponse(
        { error: { code: 'bad_request', message: 'scheduled_media_missing' } },
        400,
      );
    }
    return base ? base(input) : jsonResponse({});
  });
  renderWithClient(<SchedulePhotosModal accountId="acc-1" onClose={vi.fn()} />);

  pick([png('a.png')]);
  await userEvent.click(screen.getByRole('button', { name: 'Запланировать 1 фото' }));
  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Запланировать 1 фото' })).toBeEnabled();
  });
  await userEvent.click(screen.getByRole('button', { name: 'Запланировать 1 фото' }));

  await waitFor(() => {
    expect(log.photos).toHaveLength(1);
  });
  expect(log.uploads).toBe(2);
});

test('a retry after a failed story schedule keeps its key, edited or not', async () => {
  route();
  const base = vi.mocked(fetch).getMockImplementation();
  const keys: unknown[] = [];
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    if (request.url.endsWith('/scheduled/story')) {
      keys.push(((await request.clone().json()) as { batch_id: unknown }).batch_id);
      if (keys.length === 1) {
        return jsonResponse({ error: { code: 'unavailable', message: 'unavailable' } }, 503);
      }
    }
    return base ? base(input) : jsonResponse({});
  });
  renderWithClient(<AddStoryModal accountId="acc-1" onClose={vi.fn()} onPosted={vi.fn()} />);

  await userEvent.click(screen.getByText('По расписанию'));
  pick([png('s.png')]);
  await userEvent.click(screen.getByRole('button', { name: 'Запланировать' }));
  await waitFor(() => {
    expect(keys).toHaveLength(1);
  });
  await userEvent.type(screen.getByPlaceholderText('Введите подпись…'), 'новая');
  await userEvent.click(screen.getByRole('button', { name: 'Запланировать' }));

  await waitFor(() => {
    expect(keys).toHaveLength(2);
  });
  // The server finds the post a lost answer may have made and updates it.
  expect(keys[1]).toBe(keys[0]);
});
