import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { AddStoryModal } from './AddStoryModal';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function mockStoryOk() {
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(
      new Response(
        JSON.stringify({ status: 'ok', action_type: 'post_story', account_id: 'acc-1' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    ),
  );
}

function fileInput(): HTMLInputElement {
  return document.body.querySelector('input[type="file"]') as HTMLInputElement;
}

function img(name: string): File {
  return new File(['x'], name, { type: 'image/jpeg' });
}

// Read back the multipart body of the most recent POST .../story request.
async function lastStoryBody(): Promise<FormData> {
  const call = vi.mocked(fetch).mock.calls.find(([request]) => {
    const req = request as Request;
    return req.url.endsWith('/accounts/acc-1/story') && req.method === 'POST';
  });
  if (!call) throw new Error('no story POST captured');
  return (call[0] as Request).clone().formData();
}

test('audience, caption and no-forward interact and the modal closes', async () => {
  const onClose = vi.fn();
  renderWithClient(<AddStoryModal accountId="acc-1" onClose={onClose} onPosted={vi.fn()} />);
  expect(screen.getByText('Новая сторис')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Близкие друзья'));
  await userEvent.click(screen.getByText('Публично'));
  await userEvent.click(screen.getByText('Контакты'));

  await userEvent.click(screen.getByText('Запретить пересылку сторис'));

  const caption = screen.getByPlaceholderText('Введите подпись…');
  await userEvent.type(caption, 'привет');
  expect(caption).toHaveValue('привет');

  await userEvent.click(screen.getByLabelText('Закрыть'));
  expect(onClose).toHaveBeenCalled();
});

test('picking media and publishing posts the story', async () => {
  mockStoryOk();
  const onClose = vi.fn();
  const onPosted = vi.fn();
  renderWithClient(<AddStoryModal accountId="acc-1" onClose={onClose} onPosted={onPosted} />);

  fireEvent.change(fileInput(), { target: { files: [img('s.jpg')] } });
  await userEvent.click(screen.getByText('Опубликовать'));

  await waitFor(() => {
    const posted = vi.mocked(fetch).mock.calls.some(([request]) => {
      const req = request as Request;
      return req.url.endsWith('/accounts/acc-1/story') && req.method === 'POST';
    });
    expect(posted).toBe(true);
  });
  await waitFor(() => {
    expect(onPosted).toHaveBeenCalled();
  });
});

test('a failed publish surfaces the backend error reason on the row', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const req = input as Request;
    if (req.url.endsWith('/accounts/acc-1/story') && req.method === 'POST') {
      return Promise.resolve(
        new Response(
          JSON.stringify({ error: { code: 'bad_request', message: 'Proxy connection timed out' } }),
          { status: 400, headers: { 'Content-Type': 'application/json' } },
        ),
      );
    }
    return Promise.resolve(
      new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
  });
  renderWithClient(<AddStoryModal accountId="acc-1" onClose={vi.fn()} onPosted={vi.fn()} />);
  fireEvent.change(fileInput(), { target: { files: [img('s.jpg')] } });
  await userEvent.click(screen.getByText('Опубликовать'));
  // The red-icon tooltip carries the real reason (was a generic "Ошибка").
  expect(await screen.findByText('Proxy connection timed out')).toBeInTheDocument();
});

test('a locale-neutral failure code translates to user-facing copy', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const req = input as Request;
    if (req.url.endsWith('/accounts/acc-1/story') && req.method === 'POST') {
      return Promise.resolve(
        new Response(
          JSON.stringify({ error: { code: 'bad_request', message: 'story_image_invalid' } }),
          { status: 400, headers: { 'Content-Type': 'application/json' } },
        ),
      );
    }
    return Promise.resolve(
      new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
  });
  renderWithClient(<AddStoryModal accountId="acc-1" onClose={vi.fn()} onPosted={vi.fn()} />);
  fireEvent.change(fileInput(), { target: { files: [img('s.jpg')] } });
  await userEvent.click(screen.getByText('Опубликовать'));
  // The raw code never leaks — the RU copy from accounts.addStory.code.* shows.
  expect(
    await screen.findByText('Изображение не удалось прочитать — выберите JPG/PNG/WebP'),
  ).toBeInTheDocument();
  expect(screen.queryByText('story_image_invalid')).not.toBeInTheDocument();
});

test('the picked video size uses localized units, not hardcoded RU', async () => {
  renderWithClient(<AddStoryModal accountId="acc-1" onClose={vi.fn()} onPosted={vi.fn()} />);
  // ~2 MB video → the localized "МБ" unit (default RU locale).
  const big = new File([new Uint8Array(2_200_000)], 'clip.mp4', { type: 'video/mp4' });
  fireEvent.change(fileInput(), { target: { files: [big] } });
  expect(await screen.findByText(/МБ$/)).toBeInTheDocument();
});

test('a picked photo shows a removable tile', async () => {
  renderWithClient(<AddStoryModal accountId="acc-1" onClose={vi.fn()} onPosted={vi.fn()} />);
  fireEvent.change(fileInput(), { target: { files: [img('s.jpg')] } });
  expect(await screen.findByAltText('s.jpg')).toBeInTheDocument();
  await userEvent.click(screen.getByLabelText('Убрать фото 1'));
  await waitFor(() => {
    expect(screen.queryByAltText('s.jpg')).not.toBeInTheDocument();
  });
});

test('multi-select accumulates photos in order across picks', async () => {
  renderWithClient(<AddStoryModal accountId="acc-1" onClose={vi.fn()} onPosted={vi.fn()} />);
  fireEvent.change(fileInput(), { target: { files: [img('a.jpg'), img('b.jpg')] } });
  fireEvent.change(fileInput(), { target: { files: [img('c.jpg')] } });
  const tiles = await screen.findAllByRole('img');
  expect(tiles.map((el) => el.getAttribute('alt'))).toEqual(['a.jpg', 'b.jpg', 'c.jpg']);
});

test('the handler reads files before the live FileList is cleared on reset', async () => {
  renderWithClient(<AddStoryModal accountId="acc-1" onClose={vi.fn()} onPosted={vi.fn()} />);
  // Real browsers: input.files is a live FileList and value='' EMPTIES it in
  // place. The handler must materialize files before the reset, or this drops
  // to zero photos (the shipped-and-reverted bug).
  const input = fileInput();
  const live: File[] = [img('s.jpg')];
  Object.defineProperty(input, 'files', { configurable: true, get: () => live });
  Object.defineProperty(input, 'value', {
    configurable: true,
    get: () => '',
    set: () => {
      live.length = 0;
    },
  });
  fireEvent.change(input);
  expect(await screen.findByAltText('s.jpg')).toBeInTheDocument();
});

test('reorder buttons change the order of files sent', async () => {
  mockStoryOk();
  renderWithClient(<AddStoryModal accountId="acc-1" onClose={vi.fn()} onPosted={vi.fn()} />);
  fireEvent.change(fileInput(), {
    target: { files: [img('a.jpg'), img('b.jpg'), img('c.jpg')] },
  });
  // Move photo 1 (a.jpg) one step right → [b, a, c].
  await userEvent.click(await screen.findByLabelText('Переместить фото 1 вправо'));
  await userEvent.click(screen.getByText('Опубликовать'));

  await waitFor(async () => {
    const body = await lastStoryBody();
    const names = body.getAll('files').map((f) => (f as File).name);
    expect(names).toEqual(['b.jpg', 'a.jpg', 'c.jpg']);
  });
});

test('the layout picker appears at 2+ photos and is hidden for a single photo', async () => {
  renderWithClient(<AddStoryModal accountId="acc-1" onClose={vi.fn()} onPosted={vi.fn()} />);
  fireEvent.change(fileInput(), { target: { files: [img('a.jpg')] } });
  await screen.findByAltText('a.jpg');
  expect(screen.queryByText('Раскладка коллажа')).not.toBeInTheDocument();

  fireEvent.change(fileInput(), { target: { files: [img('b.jpg')] } });
  expect(await screen.findByText('Раскладка коллажа')).toBeInTheDocument();
  expect(screen.getByLabelText('Раскладка v2')).toBeInTheDocument();
  expect(screen.getByLabelText('Раскладка h2')).toBeInTheDocument();
});

test('selecting a layout sends the chosen collage_layout', async () => {
  mockStoryOk();
  renderWithClient(<AddStoryModal accountId="acc-1" onClose={vi.fn()} onPosted={vi.fn()} />);
  fireEvent.change(fileInput(), { target: { files: [img('a.jpg'), img('b.jpg')] } });
  await userEvent.click(await screen.findByLabelText('Раскладка h2'));
  await userEvent.click(screen.getByText('Опубликовать'));

  await waitFor(async () => {
    const body = await lastStoryBody();
    expect(body.get('collage_layout')).toBe('h2');
  });
});

test('changing the photo count resets the layout to that count default', async () => {
  mockStoryOk();
  renderWithClient(<AddStoryModal accountId="acc-1" onClose={vi.fn()} onPosted={vi.fn()} />);
  // Two photos → pick the non-default h2, then add a third → h2 is invalid for
  // 3, so the selection snaps to the count-3 default (v3).
  fireEvent.change(fileInput(), { target: { files: [img('a.jpg'), img('b.jpg')] } });
  await userEvent.click(await screen.findByLabelText('Раскладка h2'));
  fireEvent.change(fileInput(), { target: { files: [img('c.jpg')] } });
  await userEvent.click(screen.getByText('Опубликовать'));

  await waitFor(async () => {
    const body = await lastStoryBody();
    expect(body.get('collage_layout')).toBe('v3');
  });
});

test('a three-photo collage publishes ordered files, image kind and a layout', async () => {
  mockStoryOk();
  renderWithClient(<AddStoryModal accountId="acc-1" onClose={vi.fn()} onPosted={vi.fn()} />);
  fireEvent.change(fileInput(), {
    target: { files: [img('a.jpg'), img('b.jpg'), img('c.jpg')] },
  });
  await screen.findByAltText('c.jpg');
  await userEvent.click(screen.getByText('Опубликовать'));

  await waitFor(async () => {
    const body = await lastStoryBody();
    expect(body.getAll('files').map((f) => (f as File).name)).toEqual(['a.jpg', 'b.jpg', 'c.jpg']);
    expect(body.get('media_kind')).toBe('image');
    expect(body.get('collage_layout')).toBe('v3');
  });
});

test('a video stays single-media: no tiles, no layout, no collage_layout', async () => {
  mockStoryOk();
  renderWithClient(<AddStoryModal accountId="acc-1" onClose={vi.fn()} onPosted={vi.fn()} />);
  const clip = new File(['v'], 'clip.mp4', { type: 'video/mp4' });
  fireEvent.change(fileInput(), { target: { files: [clip] } });
  expect(await screen.findByText('clip.mp4')).toBeInTheDocument();
  expect(screen.queryByText('Раскладка коллажа')).not.toBeInTheDocument();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();

  await userEvent.click(screen.getByText('Опубликовать'));
  await waitFor(async () => {
    const body = await lastStoryBody();
    expect(body.getAll('files').map((f) => (f as File).name)).toEqual(['clip.mp4']);
    expect(body.get('media_kind')).toBe('video');
    expect(body.get('collage_layout')).toBeNull();
  });
});

test('picking a video after photos replaces them (no mixing)', async () => {
  renderWithClient(<AddStoryModal accountId="acc-1" onClose={vi.fn()} onPosted={vi.fn()} />);
  fireEvent.change(fileInput(), { target: { files: [img('a.jpg'), img('b.jpg')] } });
  await screen.findByAltText('a.jpg');
  const clip = new File(['v'], 'clip.mp4', { type: 'video/mp4' });
  fireEvent.change(fileInput(), { target: { files: [clip] } });
  expect(await screen.findByText('clip.mp4')).toBeInTheDocument();
  expect(screen.queryByAltText('a.jpg')).not.toBeInTheDocument();
});
