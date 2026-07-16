import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { Toaster } from '@/shared/ui';

import { VIDEO_MAX_BYTES } from './_channelsShared';
import { ChannelPostsPanel } from './ChannelPostsPanel';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      {ui}
      <Toaster />
    </QueryClientProvider>,
  );
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const PAGE_ONE = {
  items: [
    { post_id: 10, date_unix: 1_700_000_000, text: 'Второй пост', media_kind: 'photo', views: 10 },
    { post_id: 9, date_unix: 1_690_000_000, text: 'Первый пост', media_kind: 'none', views: null },
  ],
  next_cursor: 'cur1',
};

const PAGE_TWO = {
  items: [
    { post_id: 8, date_unix: 1_680_000_000, text: 'Старый пост', media_kind: 'none', views: 3 },
  ],
  next_cursor: null,
};

const POSTS_PATH = '/api/v1/accounts/acc-1/channels/123/posts';

function routeApi() {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === POSTS_PATH && request.method === 'GET') {
      return Promise.resolve(
        jsonResponse(url.searchParams.get('cursor') === 'cur1' ? PAGE_TWO : PAGE_ONE),
      );
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
}

function requests(fragment: string, method = 'POST'): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter(
      (request) => new URL(request.url).pathname.endsWith(fragment) && request.method === method,
    );
}

function composer(): HTMLTextAreaElement {
  return screen.getByPlaceholderText('Текст поста…') as HTMLTextAreaElement;
}

function fileInput(): HTMLInputElement {
  return document.body.querySelector('input[type="file"]') as HTMLInputElement;
}

test('renders the post history with media kind and views', async () => {
  routeApi();
  renderWithClient(<ChannelPostsPanel accountId="acc-1" channelId="123" />);
  expect(await screen.findByText('Второй пост')).toBeInTheDocument();
  expect(screen.getByText('Первый пост')).toBeInTheDocument();
  expect(screen.getByText('Фото')).toBeInTheDocument();
  expect(screen.getByText('10 просмотров')).toBeInTheDocument();
});

test('publishing a text-only post sends multipart text and clears the composer', async () => {
  routeApi();
  renderWithClient(<ChannelPostsPanel accountId="acc-1" channelId="123" />);
  await screen.findByText('Второй пост');

  await userEvent.type(composer(), 'Привет, канал');
  await userEvent.click(screen.getByText('Опубликовать'));

  await waitFor(() => {
    expect(requests(POSTS_PATH)).toHaveLength(1);
  });
  const form = await (requests(POSTS_PATH)[0] as Request).clone().formData();
  expect(form.get('text')).toBe('Привет, канал');
  expect(form.get('file')).toBeNull();

  // Success clears the composer and re-pulls the history.
  await waitFor(() => {
    expect(composer().value).toBe('');
  });
  await waitFor(() => {
    expect(requests(POSTS_PATH, 'GET').length).toBeGreaterThanOrEqual(2);
  });
});

test('publishing with a photo sends the file and shows a removable preview first', async () => {
  routeApi();
  renderWithClient(<ChannelPostsPanel accountId="acc-1" channelId="123" />);
  await screen.findByText('Второй пост');

  fireEvent.change(fileInput(), {
    target: { files: [new File(['x'], 'p.jpg', { type: 'image/jpeg' })] },
  });
  expect(await screen.findByAltText('p.jpg')).toBeInTheDocument();

  await userEvent.type(composer(), 'Подпись');
  await userEvent.click(screen.getByText('Опубликовать'));

  await waitFor(() => {
    expect(requests(POSTS_PATH)).toHaveLength(1);
  });
  const form = await (requests(POSTS_PATH)[0] as Request).clone().formData();
  expect(form.get('text')).toBe('Подпись');
  expect((form.get('file') as File).name).toBe('p.jpg');
});

test('a wrong-type or oversized file is rejected client-side with a toast', async () => {
  routeApi();
  renderWithClient(<ChannelPostsPanel accountId="acc-1" channelId="123" />);
  await screen.findByText('Второй пост');

  fireEvent.change(fileInput(), {
    target: { files: [new File(['x'], 'doc.pdf', { type: 'application/pdf' })] },
  });
  expect(await screen.findByText(/«doc\.pdf» пропущен/)).toBeInTheDocument();

  const hugeVideo = new File(['x'], 'movie.mp4', { type: 'video/mp4' });
  Object.defineProperty(hugeVideo, 'size', { value: VIDEO_MAX_BYTES + 1 });
  fireEvent.change(fileInput(), { target: { files: [hugeVideo] } });
  expect(await screen.findByText(/«movie\.mp4» пропущен/)).toBeInTheDocument();

  expect(requests(POSTS_PATH)).toHaveLength(0);
});

test('a staged video shows as a filename row and can be removed', async () => {
  routeApi();
  renderWithClient(<ChannelPostsPanel accountId="acc-1" channelId="123" />);
  await screen.findByText('Второй пост');

  fireEvent.change(fileInput(), {
    target: { files: [new File(['x'], 'clip.mov', { type: 'video/quicktime' })] },
  });
  expect(await screen.findByText('clip.mov')).toBeInTheDocument();
  expect(screen.queryByAltText('clip.mov')).not.toBeInTheDocument();

  await userEvent.click(screen.getByLabelText('Убрать файл'));
  await waitFor(() => {
    expect(screen.queryByText('clip.mov')).not.toBeInTheDocument();
  });
});

test('load more fetches the next cursor page and appends it', async () => {
  routeApi();
  renderWithClient(<ChannelPostsPanel accountId="acc-1" channelId="123" />);
  await screen.findByText('Второй пост');

  await userEvent.click(screen.getByText('Показать ещё'));
  expect(await screen.findByText('Старый пост')).toBeInTheDocument();
  // The head pages stay — the tail is appended, not replaced.
  expect(screen.getByText('Второй пост')).toBeInTheDocument();
  // The last page has no cursor → the button disappears.
  expect(screen.queryByText('Показать ещё')).not.toBeInTheDocument();

  const paged = requests(POSTS_PATH, 'GET').filter(
    (request) => new URL(request.url).searchParams.get('cursor') === 'cur1',
  );
  expect(paged).toHaveLength(1);
});

test('inline edit sends the new text to the edit endpoint', async () => {
  routeApi();
  renderWithClient(<ChannelPostsPanel accountId="acc-1" channelId="123" />);
  await screen.findByText('Второй пост');

  await userEvent.click(screen.getAllByLabelText('Редактировать пост')[0] as HTMLElement);
  const editArea = screen.getByDisplayValue('Второй пост');
  await userEvent.clear(editArea);
  await userEvent.type(editArea, 'Обновлено');
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    expect(requests('/posts/10/edit')).toHaveLength(1);
  });
  const body = (await (requests('/posts/10/edit')[0] as Request).clone().json()) as Record<
    string,
    unknown
  >;
  expect(body).toEqual({ text: 'Обновлено' });
  // The editor closes and the history re-pulls.
  await waitFor(() => {
    expect(screen.queryByDisplayValue('Обновлено')).not.toBeInTheDocument();
  });
});

test('a failed edit surfaces the translated stable code inline', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === POSTS_PATH && request.method === 'GET') {
      return Promise.resolve(jsonResponse(PAGE_ONE));
    }
    if (url.pathname.endsWith('/posts/10/edit')) {
      return Promise.resolve(
        jsonResponse({ error: { code: 'bad_request', message: 'message_edit_time_expired' } }, 400),
      );
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ChannelPostsPanel accountId="acc-1" channelId="123" />);
  await screen.findByText('Второй пост');

  await userEvent.click(screen.getAllByLabelText('Редактировать пост')[0] as HTMLElement);
  await userEvent.click(screen.getByText('Сохранить'));

  expect(await screen.findByText('Время редактирования поста истекло')).toBeInTheDocument();
});

test('deleting a post asks for confirmation and fires the endpoint', async () => {
  routeApi();
  renderWithClient(<ChannelPostsPanel accountId="acc-1" channelId="123" />);
  await screen.findByText('Второй пост');

  await userEvent.click(screen.getAllByLabelText('Удалить пост')[0] as HTMLElement);
  expect(await screen.findByText('Удалить пост?')).toBeInTheDocument();
  await userEvent.click(screen.getByText('Удалить', { selector: 'button' }));

  await waitFor(() => {
    expect(requests('/posts/10/delete')).toHaveLength(1);
  });
  await waitFor(() => {
    expect(screen.queryByText('Удалить пост?')).not.toBeInTheDocument();
  });
});

test('the publish button is locked while a publish is in flight (no double post)', async () => {
  let resolvePublish!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === POSTS_PATH && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
    }
    if (url.pathname === POSTS_PATH && request.method === 'POST') {
      return new Promise((resolve) => {
        resolvePublish = resolve;
      });
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ChannelPostsPanel accountId="acc-1" channelId="123" />);
  await screen.findByText('Постов пока нет');

  await userEvent.type(composer(), 'Один раз');
  const publishBtn = screen.getByText('Опубликовать');
  await userEvent.click(publishBtn);

  await waitFor(() => {
    expect(screen.getByText('Публикация…')).toBeInTheDocument();
  });
  // A second click while pending must not fire a second POST.
  await userEvent.click(screen.getByText('Публикация…'));
  expect(requests(POSTS_PATH)).toHaveLength(1);

  resolvePublish(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  await waitFor(() => {
    expect(composer().value).toBe('');
  });
});

test('a failed history load shows a retryable error', async () => {
  let failing = true;
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === POSTS_PATH && request.method === 'GET') {
      return Promise.resolve(failing ? jsonResponse({}, 500) : jsonResponse(PAGE_ONE));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ChannelPostsPanel accountId="acc-1" channelId="123" />);
  // No stable code in the envelope → the generic fallback copy shows.
  expect(await screen.findByText('Не удалось загрузить посты')).toBeInTheDocument();

  failing = false;
  await userEvent.click(screen.getByText('Повторить'));
  expect(await screen.findByText('Второй пост')).toBeInTheDocument();
});
