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

// The fleet the callers pass: one account with a photo and a handle, one without either.
const FLEET = {
  'acc-1': {
    account_id: 'acc-1',
    first_name: 'Алиса',
    last_name: 'Смирнова',
    username: 'alisa',
    avatar_etag: 'e1',
  },
  'acc-2': { account_id: 'acc-2', first_name: 'Борис', phone: '+79990001122' },
} as const;
const accountOf = (id: string) => FLEET[id as keyof typeof FLEET];

test('rows render translated and the counter follows the filter', async () => {
  render(
    <LogTerminal
      title="Лог"
      logLines={[
        entry({ id: 1, event: 'neuroshilling_message_sent' }),
        entry({ id: 2, account_id: 'acc-2', event: 'neuroshilling_run_stopped' }),
      ]}
      accountOf={accountOf}
    />,
  );
  expect(screen.getByText('2')).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'Алиса Смирнова · @alisa' }));
  expect(screen.queryByText('Кампания завершена')).toBeNull();
  expect(screen.getByText('1')).toBeInTheDocument();
  // The filter chip names the account in words — it is a header, not a column.
  expect(screen.getByText('Только Алиса Смирнова ×')).toBeInTheDocument();

  await userEvent.click(screen.getByTitle('Показать все'));
  expect(screen.getByText('Кампания завершена')).toBeInTheDocument();
});

test('a known account is its face: the photo when it has one, else initials', () => {
  const { container } = render(
    <LogTerminal
      title="Лог"
      logLines={[entry({ id: 1 }), entry({ id: 2, account_id: 'acc-2' })]}
      accountOf={accountOf}
    />,
  );
  const withPhoto = screen.getByRole('button', { name: 'Алиса Смирнова · @alisa' });
  // The hover says who it is — the name is no longer printed in the row.
  expect(withPhoto).toHaveAttribute('title', 'Алиса Смирнова · @alisa');
  expect(withPhoto.querySelector('img')).toHaveAttribute(
    'src',
    '/api/v1/accounts/acc-1/avatar?v=e1',
  );
  expect(screen.queryByText('Алиса Смирнова')).toBeNull();

  // No handle → the display name alone; no photo → the initials circle.
  const initials = screen.getByRole('button', { name: 'Борис' });
  expect(initials).toHaveAttribute('title', 'Борис');
  expect(initials.querySelector('img')).toBeNull();
  expect(initials).toHaveTextContent('Б');
  expect(container.querySelectorAll('img')).toHaveLength(1);
});

test('an account the fleet does not know is a neutral face named by its id, and still filters', async () => {
  render(
    <LogTerminal
      title="Лог"
      logLines={[
        entry({ id: 1, account_id: 'gone-7', event: 'neuroshilling_message_sent' }),
        entry({ id: 2, account_id: 'acc-2', event: 'neuroshilling_run_stopped' }),
        entry({ id: 3, account_id: null, event: 'neuroshilling_run_started' }),
      ]}
      accountOf={accountOf}
    />,
  );
  const unknown = screen.getByRole('button', { name: 'gone-7' });
  expect(unknown.querySelector('img')).toBeNull();
  expect(unknown).toHaveAttribute('title', 'gone-7 · Только этот аккаунт');
  // The same circle as a known account's and the spacer of an account-less row, so the
  // columns after it line up in a mixed feed.
  expect(unknown.className).toContain('size-glyph');
  expect(unknown).toHaveTextContent('');
  expect(screen.getByRole('button', { name: 'Борис' }).querySelector('.size-glyph')).not.toBeNull();
  // Three rows, two account buttons: the account-less row renders a spacer, not a button.
  expect(screen.getAllByRole('button', { name: /gone-7|Борис/ })).toHaveLength(2);

  await userEvent.click(unknown);
  expect(screen.queryByText('Кампания завершена')).toBeNull();
  expect(screen.getByText('Только gone-7 ×')).toBeInTheDocument();
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
