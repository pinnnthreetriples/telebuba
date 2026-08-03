import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { LogEntry } from '@/shared/api';

import { ActivityLogCard } from './ActivityLogCard';

function entry(over: Partial<LogEntry>): LogEntry {
  return {
    id: 1,
    created_at: '2026-07-11T10:00:00+00:00',
    level: 'INFO',
    status: 'success',
    account_id: 'acc-1',
    event: 'neurocomment_posted',
    extra: {},
    ...over,
  };
}

test('shows the channel and the translated reason inline', () => {
  render(
    <ActivityLogCard
      logLines={[
        entry({
          id: 1,
          event: 'neurocomment_no_account_available',
          extra: { channel: '@Barca_Studio_News', reason: 'quota' },
        }),
      ]}
    />,
  );
  expect(screen.getByText('@Barca_Studio_News')).toBeInTheDocument();
  expect(screen.getByText('Нет доступного аккаунта')).toBeInTheDocument();
  expect(screen.getByText(/лимит исчерпан/)).toBeInTheDocument();
});

test('names the account behind each line, and stays quiet when the row has none', () => {
  render(
    <ActivityLogCard
      logLines={[
        entry({ id: 1, event: 'neurocomment_onboard_join_by_request', extra: { channel: '@a' } }),
        entry({ id: 2, account_id: null, event: 'neurocomment_listener_started' }),
      ]}
      accountName={(id) => (id === 'acc-1' ? 'Alisa' : id)}
    />,
  );
  // getByText throws on a second match, so this also pins the account-less row to blank.
  expect(screen.getByText('Alisa')).toBeInTheDocument();
});

test('clicking an account narrows the feed to it, and the chip restores everything', async () => {
  render(
    <ActivityLogCard
      logLines={[
        entry({ id: 1, event: 'neurocomment_posted' }),
        entry({ id: 2, account_id: 'acc-2', event: 'neurocomment_channel_comments_off' }),
      ]}
      accountName={(id) => (id === 'acc-1' ? 'Alisa' : 'Мария')}
    />,
  );
  await userEvent.click(screen.getByRole('button', { name: 'Alisa' }));
  expect(screen.queryByText('Комментарии в канале отключены')).toBeNull();
  expect(screen.getByText('Комментарий опубликован')).toBeInTheDocument();

  await userEvent.click(screen.getByTitle('Показать все'));
  expect(screen.getByText('Комментарии в канале отключены')).toBeInTheDocument();
});

test('a failed post shows the Telegram error type next to the translated reason', () => {
  // `status: "failed"` always translates, so the error type has to sit ALONGSIDE the
  // reason — behind it as a fallback it would never render, and the line would keep
  // reporting a failure without its cause.
  render(
    <ActivityLogCard
      logLines={[
        entry({
          level: 'WARNING',
          status: 'warning',
          event: 'neurocomment_post_failed',
          extra: { channel: '@chan', status: 'failed', error_type: 'SomeUnmappedRpcError' },
        }),
      ]}
    />,
  );
  expect(screen.getByText('· Telegram отклонил · SomeUnmappedRpcError')).toBeInTheDocument();
});

test('a failed join says in words what Telegram refused, and against which channel', () => {
  // The row an operator could not read: "Вступление в чат канала — ошибка ·
  // ChannelPrivateError" named neither the channel nor anything they could act on.
  render(
    <ActivityLogCard
      logLines={[
        entry({
          level: 'ERROR',
          status: 'error',
          event: 'neurocomment_telegram_join_discussion_group_failed',
          extra: { channel: '@MeineDNEWS', error_type: 'ChannelPrivateError' },
        }),
      ]}
    />,
  );
  expect(screen.getByText('@MeineDNEWS')).toBeInTheDocument();
  expect(screen.getByText('Вступление в чат канала — ошибка')).toBeInTheDocument();
  expect(screen.getByText('· чат закрыт: не пускают или выгнали')).toBeInTheDocument();
});

test('spells out a gateway stable code, which arrives instead of an exception class', () => {
  render(
    <ActivityLogCard
      logLines={[
        entry({
          id: 1,
          level: 'ERROR',
          status: 'error',
          event: 'neurocomment_telegram_join_channel_failed',
          extra: { channel: '@a', error_type: 'session_dead' },
        }),
        entry({
          id: 2,
          level: 'ERROR',
          status: 'error',
          event: 'neurocomment_telegram_post_comment_failed',
          extra: { channel: '@b', error_type: 'chat_admin_required' },
        }),
      ]}
    />,
  );
  expect(
    screen.getByText('· Сессия аккаунта в Telegram недействительна — войдите заново'),
  ).toBeInTheDocument();
  expect(screen.getByText('· Нужны права администратора канала')).toBeInTheDocument();
});

test('colours an attempted-but-failed event red even though it is logged INFO', () => {
  render(
    <ActivityLogCard
      logLines={[
        entry({ event: 'neurocomment_generation_exhausted', extra: { reason: 'gemini_error' } }),
      ]}
    />,
  );
  const label = screen.getByText('Не удалось сгенерировать текст');
  expect(label).toHaveStyle({ color: '#e5736b' });
});

test('shows a clear-log button only with onClear and rows, and fires it', async () => {
  const onClear = vi.fn();
  const { rerender } = render(<ActivityLogCard logLines={[]} onClear={onClear} />);
  // No rows → nothing to clear, button hidden.
  expect(screen.queryByRole('button', { name: 'Очистить лог' })).toBeNull();

  rerender(<ActivityLogCard logLines={[entry({})]} onClear={onClear} />);
  await userEvent.click(screen.getByRole('button', { name: 'Очистить лог' }));
  expect(onClear).toHaveBeenCalledTimes(1);
});

test('omits the clear button when no onClear is given', () => {
  render(<ActivityLogCard logLines={[entry({})]} />);
  expect(screen.queryByRole('button', { name: 'Очистить лог' })).toBeNull();
});

test('attaches a what-to-do hint as a hover tooltip', () => {
  render(
    <ActivityLogCard
      logLines={[entry({ event: 'neurocomment_no_account_available', extra: { channel: '@x' } })]}
    />,
  );
  const row = screen.getByText('Нет доступного аккаунта').closest('div');
  expect(row?.getAttribute('title')).toMatch(/Добавьте аккаунтов/);
});
