import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type {
  AccountRead,
  ChatBroadcastBoard,
  ChatBroadcastBoardRow,
  ChatBroadcastHistoryEntry,
} from '@/shared/api';

import { board, row } from '../model/fixtures.test-helpers';

import { BoardCard } from './BoardCard';

// Newest first, as the server sends it: «сообщение N» … «сообщение 1».
function history(count: number): ChatBroadcastHistoryEntry[] {
  return Array.from({ length: count }, (_, index) => ({
    at: `2026-10-06T10:${String(59 - index).padStart(2, '0')}:00Z`,
    account_id: 'a1',
    round: 1,
    kind: 'sent' as const,
    text: `сообщение ${String(count - index)}`,
  }));
}

function boardWith(long: number, short = 3): ChatBroadcastBoard {
  return board({
    rows: [
      row({ chat_key: 'alpha', raw: '@alpha', state: 'writing', history: history(long) }),
      row({ chat_key: 'beta', raw: '@beta', state: 'writing', history: history(short) }),
    ],
  });
}

function renderBoard(data: ChatBroadcastBoard, onAction = vi.fn()) {
  const props = {
    fleet: new Map<string, AccountRead>(),
    time: (iso: string) => iso.slice(11, 16),
    now: Date.parse('2026-10-06T11:00:00Z'),
    approvalHours: 24,
    busy: false,
    onAction,
    onPace: vi.fn(),
  };
  const view = render(<BoardCard board={data} {...props} />);
  return {
    rerender: (next: ChatBroadcastBoard) => {
      view.rerender(<BoardCard board={next} {...props} />);
    },
  };
}

async function openRow(index: number) {
  await userEvent.click(screen.getAllByRole('button', { name: 'История и действия' })[index]!);
}

function shown(): number {
  return screen.queryAllByText(/^сообщение \d+$/).length;
}

test('a long chat history shows its six newest entries and a button for the rest', async () => {
  renderBoard(boardWith(8));
  await openRow(0);

  expect(shown()).toBe(6);
  expect(screen.getByText('сообщение 8')).toBeInTheDocument();
  expect(screen.queryByText('сообщение 2')).toBeNull();
  // The header badge still counts the whole history.
  expect(screen.getByText('8')).toBeInTheDocument();

  const more = screen.getByRole('button', { name: 'Показать всю историю · 8' });
  expect(more).toHaveAttribute('aria-expanded', 'false');

  await userEvent.click(more);
  expect(shown()).toBe(8);
  const less = screen.getByRole('button', { name: 'Свернуть' });
  expect(less).toHaveAttribute('aria-expanded', 'true');

  await userEvent.click(less);
  expect(shown()).toBe(6);
  expect(screen.getByRole('button', { name: 'Показать всю историю · 8' })).toBeInTheDocument();
});

test('a history of six or fewer entries has no button', async () => {
  renderBoard(boardWith(8, 6));
  await openRow(1);

  expect(shown()).toBe(6);
  expect(screen.queryByRole('button', { name: /Показать всю историю/ })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Свернуть' })).toBeNull();
});

test('an opened history stays opened when the board refetches', async () => {
  const view = renderBoard(boardWith(8));
  await openRow(0);
  await userEvent.click(screen.getByRole('button', { name: 'Показать всю историю · 8' }));
  expect(shown()).toBe(8);

  view.rerender(boardWith(9));

  expect(screen.getByText('сообщение 9')).toBeInTheDocument();
  expect(shown()).toBe(9);
  expect(screen.getByRole('button', { name: 'Свернуть' })).toHaveAttribute('aria-expanded', 'true');
});

function skipped(over: Partial<ChatBroadcastBoardRow> = {}): ChatBroadcastBoard {
  return board({
    rows: [
      row({
        chat_key: 'delta',
        raw: '@delta',
        state: 'skipped',
        skip_reason: 'deleted',
        message_deleted: true,
        active: false,
        ...over,
      }),
    ],
  });
}

test('a chat skipped for a deleted message offers to keep writing, and only that', async () => {
  const onAction = vi.fn();
  renderBoard(skipped(), onAction);
  await openRow(0);

  expect(screen.queryByRole('button', { name: 'Написать сейчас' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Пропустить чат' })).toBeNull();
  await userEvent.click(screen.getByRole('button', { name: 'Писать всё равно' }));
  expect(onAction).toHaveBeenCalledWith(expect.objectContaining({ chat_key: 'delta' }), {
    kind: 'keep',
  });
});

test('keeping is disabled while busy and absent for other skip reasons', async () => {
  const { unmount } = render(
    <BoardCard
      board={skipped()}
      fleet={new Map()}
      time={(iso) => iso}
      now={0}
      approvalHours={24}
      busy
      onAction={vi.fn()}
      onPace={vi.fn()}
    />,
  );
  await openRow(0);
  expect(screen.getByRole('button', { name: 'Писать всё равно' })).toBeDisabled();
  unmount();

  renderBoard(skipped({ skip_reason: 'admin_only', message_deleted: false }));
  await openRow(0);
  expect(screen.queryByRole('button', { name: 'Писать всё равно' })).toBeNull();
});

test('a kept chat loses the button and wears one badge beside «удалено»', async () => {
  renderBoard(skipped({ ignore_deleted: true }));
  await openRow(0);

  expect(screen.queryByRole('button', { name: 'Писать всё равно' })).toBeNull();
  expect(screen.getByText('удалено')).toBeInTheDocument();
  expect(screen.getByText('пишем несмотря на удаления')).toBeInTheDocument();
});

test('a finished campaign only flags the chat: it is written on the next run', () => {
  const data = skipped({ ignore_deleted: true });
  renderBoard({ ...data, campaign: { ...data.campaign, status: 'done' } });

  expect(screen.getByText('напишем в следующем запуске')).toBeInTheDocument();
  expect(screen.queryByText('пишем несмотря на удаления')).toBeNull();
});
