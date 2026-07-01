import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { NeuroAccountsModal, type NeuroAccountRow } from './NeuroAccountsModal';

const ACCOUNTS: NeuroAccountRow[] = [
  { account_id: 'a1', phone: '+79990000001', channel: '@crypto' },
  { account_id: 'a2', phone: '+79990000002', channel: null },
];

test('assigns an idle account, confirms removal, and closes', async () => {
  const onClose = vi.fn();
  const onPick = vi.fn();
  const onRemove = vi.fn();
  render(
    <NeuroAccountsModal accounts={ACCOUNTS} onClose={onClose} onPick={onPick} onRemove={onRemove} />,
  );
  expect(screen.getByText('Аккаунты в нейрокомментинге')).toBeInTheDocument();
  // an already-assigned account shows its real channel as a static label,
  // not an editable dropdown (there's no backend concept of picking one)
  expect(screen.getByText('@crypto')).toBeInTheDocument();

  // assign the idle account to the campaign
  await userEvent.click(screen.getByText('Добавить в кампанию'));
  expect(onPick).toHaveBeenCalledWith('a2');

  // removing asks for confirmation before calling onRemove
  await userEvent.click(screen.getAllByLabelText('Убрать из нейрокомментинга')[0]!);
  expect(onRemove).not.toHaveBeenCalled();
  await userEvent.click(screen.getByText('Убрать', { selector: 'button' }));
  expect(onRemove).toHaveBeenCalledWith('a1');

  await userEvent.click(screen.getByText('Готово'));
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('empty list shows the empty hint', () => {
  render(<NeuroAccountsModal accounts={[]} onClose={vi.fn()} onPick={vi.fn()} onRemove={vi.fn()} />);
  expect(screen.getByText('Нет аккаунтов в нейрокомментинге')).toBeInTheDocument();
});

test('shows a success or error mark from the feedback map', () => {
  // Modal content is rendered via a portal onto document.body, not inside
  // the render() container — query the document instead.
  const { rerender } = render(
    <NeuroAccountsModal
      accounts={ACCOUNTS}
      onClose={vi.fn()}
      onPick={vi.fn()}
      onRemove={vi.fn()}
      feedback={{ a1: 'ok' }}
    />,
  );
  expect(document.querySelector('.text-success svg')).toBeInTheDocument();

  rerender(
    <NeuroAccountsModal
      accounts={ACCOUNTS}
      onClose={vi.fn()}
      onPick={vi.fn()}
      onRemove={vi.fn()}
      feedback={{ a1: 'err' }}
    />,
  );
  expect(document.querySelector('.text-danger svg')).toBeInTheDocument();
});
