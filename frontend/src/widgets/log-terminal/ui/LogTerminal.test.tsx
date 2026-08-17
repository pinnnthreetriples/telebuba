import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { LogEntry } from '@/shared/api';

import { LogTerminal } from './LogTerminal';

function entry(over: Partial<LogEntry>): LogEntry {
  return {
    id: 1,
    created_at: '2026-07-11T10:00:00+00:00',
    level: 'INFO',
    status: 'success',
    account_id: 'acc-1',
    event: 'neuroshilling_message_sent',
    extra: {},
    ...over,
  };
}

test('the title is the caller s, in the header and as the collapse label', () => {
  render(<LogTerminal title="Лог кампании" logLines={[]} />);
  // Header text and the chevron's accessible name, so two callers on one screen
  // are told apart by a keyboard operator too.
  expect(screen.getByText('Лог кампании')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Лог кампании' })).toBeInTheDocument();
});

test('the generic strings come from the shared namespace, not a page s', () => {
  render(<LogTerminal title="Лог" logLines={[]} />);
  expect(screen.getByText('Событий пока нет')).toBeInTheDocument();
});

test('rows render translated and the counter follows the filter', async () => {
  render(
    <LogTerminal
      title="Лог"
      logLines={[
        entry({ id: 1, event: 'neuroshilling_message_sent' }),
        entry({ id: 2, account_id: 'acc-2', event: 'neuroshilling_run_stopped' }),
      ]}
      accountName={(id) => (id === 'acc-1' ? 'Алиса' : 'Борис')}
    />,
  );
  expect(screen.getByText('2')).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'Алиса' }));
  expect(screen.queryByText('Кампания завершена')).toBeNull();
  expect(screen.getByText('1')).toBeInTheDocument();

  await userEvent.click(screen.getByTitle('Показать все'));
  expect(screen.getByText('Кампания завершена')).toBeInTheDocument();
});

test('the clear button appears only with a handler and rows, and fires it', async () => {
  const onClear = vi.fn();
  const { rerender } = render(<LogTerminal title="Лог" logLines={[]} onClear={onClear} />);
  expect(screen.queryByRole('button', { name: 'Очистить лог' })).toBeNull();

  rerender(<LogTerminal title="Лог" logLines={[entry({})]} />);
  expect(screen.queryByRole('button', { name: 'Очистить лог' })).toBeNull();

  rerender(<LogTerminal title="Лог" logLines={[entry({})]} onClear={onClear} />);
  await userEvent.click(screen.getByRole('button', { name: 'Очистить лог' }));
  expect(onClear).toHaveBeenCalledTimes(1);
});
