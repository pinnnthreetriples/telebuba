import { QueryClient, QueryClientProvider, infiniteQueryOptions } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { beforeEach, expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { ChatDialog, ChatHistoryPage } from '@/shared/api';

import { AccountChats } from './AccountChats';

const api = vi.hoisted(() => ({
  markRead: vi.fn(),
  send: vi.fn(),
  listPage: vi.fn(),
  historyPage: vi.fn(),
  inboxHandler: undefined as
    undefined | ((event: { account_id: string; peer_type: 'user'; peer_id: string }) => void),
}));
vi.mock('@/entities/account/api/account-chats.queries', () => ({
  accountChatsInfiniteQueryOptions: (accountId: string) =>
    infiniteQueryOptions({
      queryKey: ['test-dialogs', accountId],
      initialPageParam: null as string | null,
      queryFn: ({ pageParam }) => api.listPage(accountId, pageParam),
      getNextPageParam: (page: { next_cursor?: string | null }) => page.next_cursor ?? undefined,
    }),
  accountChatHistoryInfiniteQueryOptions: (accountId: string, peerType: string, peerId: string) =>
    infiniteQueryOptions({
      queryKey: ['test-history', accountId, peerType, peerId],
      initialPageParam: undefined as number | undefined,
      queryFn: ({ pageParam }) => api.historyPage(accountId, pageParam),
      getNextPageParam: (page: ChatHistoryPage) => page.next_before_id ?? undefined,
    }),
}));
vi.mock('@/entities/account/api/account-chats.mutations', () => ({
  markAccountChatReadRequest: (...args: unknown[]) => api.markRead(...args),
  sendAccountChatMessageRequest: (...args: unknown[]) => api.send(...args),
  accountChatMediaFileName: (media: { file_name?: string | null }) =>
    media.file_name ?? 'attachment',
}));
vi.mock('@/shared/lib', () => ({
  useLogEventStream: vi.fn((_entry: unknown, _status: unknown, inbox: typeof api.inboxHandler) => {
    api.inboxHandler = inbox;
  }),
}));

const DIALOG: ChatDialog = {
  peer_type: 'user',
  peer_id: '100',
  title: 'Мария',
  username: 'maria',
  is_archived: false,
  unread_count: 2,
  last_message: {
    message_id: 12,
    text: 'Последнее сообщение',
    date: '2026-01-01T10:00:00Z',
    outgoing: false,
    sender_id: 100,
    media: [],
  },
};

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return { ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>), client };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.listPage.mockImplementation(async (_accountId: string, cursor: string | null) =>
    cursor ? { items: [], next_cursor: null } : { items: [DIALOG], next_cursor: 'next' },
  );
  api.historyPage.mockResolvedValue({
    items: [
      {
        message_id: 10,
        text: 'Привет',
        date: '2026-01-01T09:00:00Z',
        outgoing: false,
        sender_id: 100,
        media: [],
      },
    ],
    next_before_id: 5,
  });
  api.markRead.mockResolvedValue({ unread_count: 0 });
  api.send.mockResolvedValue({ items: [] });
});

test('loads all dialog pages and marks only the opened thread read using its newest history id', async () => {
  const user = userEvent.setup();
  renderWithClient(<AccountChats accountId="account-1" overview={<div>Обзор</div>} />);
  await user.click(screen.getByRole('tab', { name: 'Чаты' }));
  const dialog = await screen.findByRole('button', { name: /Мария/ });
  expect(dialog).toHaveTextContent('2');
  await waitFor(() =>
    expect(api.markRead).toHaveBeenCalledWith(
      expect.objectContaining({
        accountId: 'account-1',
        peerType: 'user',
        peerId: '100',
        maxMessageId: 12,
      }),
    ),
  );
  expect(api.markRead).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole('button', { name: 'Загрузить ещё диалоги' }));
  await waitFor(() => expect(api.listPage).toHaveBeenCalledWith('account-1', 'next'));
  expect(screen.getByText('Привет')).toBeInTheDocument();
});

test('overview does not load chats or acknowledge unread messages before opening Chats', async () => {
  const user = userEvent.setup();
  renderWithClient(<AccountChats accountId="account-1" overview={<div>Обзор</div>} />);
  expect(api.listPage).not.toHaveBeenCalled();
  expect(api.historyPage).not.toHaveBeenCalled();
  expect(api.markRead).not.toHaveBeenCalled();
  await user.click(screen.getByRole('tab', { name: 'Чаты' }));
  await screen.findByText('Привет');
  await waitFor(() => expect(api.markRead).toHaveBeenCalledTimes(1));
});

test('uploads at most ten selected files and reports excess before sending multipart data', async () => {
  const user = userEvent.setup();
  renderWithClient(<AccountChats accountId="account-1" overview={<div>Обзор</div>} />);
  await user.click(screen.getByRole('tab', { name: 'Чаты' }));
  await screen.findByText('Привет');
  const files = Array.from(
    { length: 11 },
    (_, index) => new File([String(index)], `file-${index}.txt`, { type: 'text/plain' }),
  );
  await user.upload(screen.getByLabelText('Прикрепить файлы'), files);
  expect(
    screen.getByText('Не прикреплено файлов: 1. За одно сообщение можно отправить не более 10.'),
  ).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Отправить' }));
  await waitFor(() =>
    expect(api.send).toHaveBeenCalledWith(
      expect.objectContaining({ files: files.slice(0, 10), text: '' }),
    ),
  );
});

test('history pagination fetches older messages and media is fetched only after an explicit click', async () => {
  const user = userEvent.setup();
  api.historyPage.mockImplementation(async (_accountId: string, beforeId: number | undefined) =>
    beforeId
      ? {
          items: [
            {
              message_id: 4,
              text: 'Ранее',
              date: '2025-12-31T09:00:00Z',
              outgoing: false,
              sender_id: 100,
              media: [],
            },
          ],
          next_before_id: null,
        }
      : {
          items: [
            {
              message_id: 10,
              text: '',
              date: '2026-01-01T09:00:00Z',
              outgoing: false,
              sender_id: 100,
              media: [
                {
                  kind: 'image',
                  file_name: 'image.png',
                  mime_type: 'image/png',
                  size: 42,
                  download_url: '/media/1',
                },
              ],
            },
            {
              message_id: 11,
              text: '',
              date: '2026-01-01T09:01:00Z',
              outgoing: false,
              sender_id: 100,
              media: [
                {
                  kind: 'document',
                  file_name: 'doc.pdf',
                  mime_type: 'application/pdf',
                  size: 42,
                  download_url: '/media/2',
                },
              ],
            },
          ],
          next_before_id: 5,
        },
  );
  renderWithClient(<AccountChats accountId="account-1" overview={<div>Обзор</div>} />);
  await user.click(screen.getByRole('tab', { name: 'Чаты' }));
  await screen.findByRole('button', { name: 'Показать более ранние сообщения' });
  expect(api.historyPage).toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'Показать более ранние сообщения' }));
  expect(await screen.findByText('Ранее')).toBeInTheDocument();
  expect(api.historyPage).toHaveBeenCalledWith('account-1', 5);
  await user.click(screen.getByRole('button', { name: 'Показать' }));
  expect(await screen.findByAltText('image.png')).toBeInTheDocument();
  expect(
    screen
      .getAllByRole('link', { name: 'Скачать' })
      .find((link) => link.getAttribute('href') === '/media/2'),
  ).toBeTruthy();
});

test('named inbox event refreshes the list and currently open history only', async () => {
  const user = userEvent.setup();
  renderWithClient(<AccountChats accountId="account-1" overview={<div>Обзор</div>} />);
  await user.click(screen.getByRole('tab', { name: 'Чаты' }));
  await screen.findByText('Привет');
  const listCalls = api.listPage.mock.calls.length;
  const historyCalls = api.historyPage.mock.calls.length;
  api.inboxHandler?.({ account_id: 'account-1', peer_type: 'user', peer_id: '100' });
  await waitFor(() => expect(api.listPage.mock.calls.length).toBeGreaterThan(listCalls));
  await waitFor(() => expect(api.historyPage.mock.calls.length).toBeGreaterThan(historyCalls));
});

test('changing the account resets the tab and never reuses the previous account peer', async () => {
  const user = userEvent.setup();
  const { rerender, client } = renderWithClient(
    <AccountChats accountId="account-1" overview={<div>Обзор 1</div>} />,
  );
  await user.click(screen.getByRole('tab', { name: 'Чаты' }));
  await screen.findByText('Привет');
  const historyRequests = api.historyPage.mock.calls.length;
  rerender(
    <QueryClientProvider client={client}>
      <AccountChats accountId="account-2" overview={<div>Обзор 2</div>} />
    </QueryClientProvider>,
  );
  await waitFor(() =>
    expect(screen.getByRole('tab', { name: 'Обзор' })).toHaveAttribute('aria-selected', 'true'),
  );
  expect(screen.queryByText('Привет')).not.toBeInTheDocument();
  expect(api.historyPage).toHaveBeenCalledTimes(historyRequests);
  expect(api.listPage.mock.calls.some(([requestAccount]) => requestAccount === 'account-2')).toBe(
    false,
  );
});

test('composer autosizes on mobile without a shrinkable flex basis', async () => {
  const user = userEvent.setup();
  renderWithClient(<AccountChats accountId="account-1" overview={<div>Обзор</div>} />);
  await user.click(screen.getByRole('tab', { name: 'Чаты' }));
  const composer = await screen.findByRole('textbox', { name: 'Напишите сообщение…' });
  expect(composer).toHaveClass('w-full', 'sm:flex-1');
  expect(composer).not.toHaveClass('flex-1');
  let scrollHeight = 40;
  Object.defineProperty(composer, 'scrollHeight', { configurable: true, get: () => scrollHeight });
  Object.assign(composer.style, { lineHeight: '20px', paddingTop: '8px', paddingBottom: '8px' });
  fireEvent.change(composer, { target: { value: 'Строка' } });
  expect(composer).toHaveStyle({ height: '40px' });
  scrollHeight = 260;
  fireEvent.change(composer, { target: { value: 'Очень длинный текст'.repeat(40) } });
  expect(composer).toHaveStyle({ height: '136px', overflowY: 'auto' });
});

test('sends text as a generated multipart request payload to the selected dialog', async () => {
  const user = userEvent.setup();
  renderWithClient(<AccountChats accountId="account-1" overview={<div>Обзор</div>} />);
  await user.click(screen.getByRole('tab', { name: 'Чаты' }));
  await screen.findByText('Привет');
  await user.type(screen.getByRole('textbox', { name: 'Напишите сообщение…' }), 'Проверка');
  await user.click(screen.getByRole('button', { name: 'Отправить' }));
  await waitFor(() =>
    expect(api.send).toHaveBeenCalledWith(
      expect.objectContaining({
        accountId: 'account-1',
        peerType: 'user',
        peerId: '100',
        text: 'Проверка',
        files: [],
      }),
    ),
  );
});

test.each([
  [
    { error: { code: 'chat_media_too_large' } },
    'Размер одного вложения превышает допустимый предел.',
  ],
  [
    { error: { code: 'payload_too_large' } },
    'Общий размер запроса с вложениями превышает допустимый предел.',
  ],
  [{ error: { code: 'too_many_files' } }, 'Можно отправить не более 10 файлов за раз.'],
])('shows a localized send restriction for %s', async (failure, message) => {
  const user = userEvent.setup();
  api.send.mockRejectedValueOnce(failure);
  renderWithClient(<AccountChats accountId="account-1" overview={<div>Обзор</div>} />);
  await user.click(screen.getByRole('tab', { name: 'Чаты' }));
  await screen.findByText('Привет');
  await user.type(screen.getByRole('textbox', { name: 'Напишите сообщение…' }), 'Тест');
  await user.click(screen.getByRole('button', { name: 'Отправить' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(message);
});
