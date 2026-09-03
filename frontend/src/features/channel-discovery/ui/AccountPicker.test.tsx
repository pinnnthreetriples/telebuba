import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import '@/shared/i18n';

import type { DiscoveryAccountOption } from '@/shared/api';
import { expectNoAxeViolations } from '@/shared/ui/axe.test-helpers';

import { MAX_SEARCH_ACCOUNTS } from '../model/filters';
import { AccountPicker } from './AccountPicker';

const ACCOUNTS: DiscoveryAccountOption[] = [
  { account_id: 'acc-p', name: 'Prem', premium: true, busy_reason: null },
  { account_id: 'acc-n', name: 'Plain', premium: false, busy_reason: null },
  { account_id: 'acc-b', name: 'Busy', premium: false, busy_reason: 'account_cooling' },
];

function Harness({
  accounts = ACCOUNTS,
  initial = ['acc-p'],
  loading = false,
  errored = false,
  onChange = vi.fn(),
}: {
  accounts?: DiscoveryAccountOption[];
  initial?: string[];
  loading?: boolean;
  errored?: boolean;
  onChange?: (ids: string[]) => void;
}) {
  const [selected, setSelected] = useState(initial);
  return (
    <AccountPicker
      accounts={accounts}
      selected={selected}
      loading={loading}
      errored={errored}
      onChange={(ids) => {
        setSelected(ids);
        onChange(ids);
      }}
    />
  );
}

const trigger = () => screen.getByRole('button', { expanded: false });

describe('AccountPicker', () => {
  it('lists every account, the busy one disabled with its reason', async () => {
    render(<Harness />);
    await userEvent.click(trigger());

    expect(screen.getAllByRole('option')).toHaveLength(3);
    const busy = screen.getByRole('option', { name: /Busy/ });
    expect(busy).toBeDisabled();
    expect(busy).toHaveTextContent('пережидает лимит');
    expect(screen.getByRole('option', { name: /Prem/ })).toHaveTextContent('Premium');
    expect(screen.getByRole('option', { name: /Plain/ })).not.toHaveTextContent('Premium');
  });

  it('shows the picked names in the trigger and counts them in the caption', () => {
    render(<Harness initial={['acc-p', 'acc-n']} />);
    expect(screen.getByRole('button', { name: 'Prem, Plain' })).toBeInTheDocument();
    expect(screen.getByText('выбрано 2')).toBeInTheDocument();
  });

  it('invites a pick when nothing is selected', () => {
    render(<Harness initial={[]} />);
    expect(screen.getByRole('button', { name: 'Выбрать аккаунты' })).toBeInTheDocument();
    expect(screen.getByText('выбрано 0')).toBeInTheDocument();
  });

  it('toggles an option in list order and keeps the list open', async () => {
    const onChange = vi.fn();
    render(<Harness initial={['acc-n']} onChange={onChange} />);
    await userEvent.click(trigger());

    await userEvent.click(screen.getByRole('option', { name: /Prem/ }));
    // List order, not click order: 'acc-p' lands before the earlier pick.
    expect(onChange).toHaveBeenLastCalledWith(['acc-p', 'acc-n']);
    expect(screen.getByRole('button', { expanded: true })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /Prem/ })).toHaveAttribute('aria-selected', 'true');

    await userEvent.click(screen.getByRole('option', { name: /Prem/ }));
    expect(onChange).toHaveBeenLastCalledWith(['acc-n']);
    expect(screen.getByText('выбран 1')).toBeInTheDocument();
  });

  it('takes no focus while collapsed and does once expanded', async () => {
    // .tb-dd collapses visually only; `inert` is what keeps the options out of the
    // Tab order while the list is closed.
    render(<Harness />);
    const collapsed = screen.getByRole('option', { name: /Plain/ });
    collapsed.focus();
    expect(collapsed).not.toHaveFocus();

    await userEvent.click(trigger());
    const expanded = screen.getByRole('option', { name: /Plain/ });
    expanded.focus();
    expect(expanded).toHaveFocus();
  });

  it('closes on Escape and hands focus back to the trigger, without reaching the dialog', async () => {
    const onDocumentKey = vi.fn();
    document.addEventListener('keydown', onDocumentKey);
    render(<Harness />);
    await userEvent.click(trigger());
    expect(screen.getByRole('button', { expanded: true })).toBeInTheDocument();

    await userEvent.keyboard('{Escape}');

    expect(trigger()).toHaveFocus();
    // The Modal's Escape listener sits on `document`; one key must not close both.
    expect(onDocumentKey).not.toHaveBeenCalled();
    document.removeEventListener('keydown', onDocumentKey);
  });

  it('closes on a click outside', async () => {
    render(
      <>
        <Harness />
        <p>outside</p>
      </>,
    );
    await userEvent.click(trigger());
    await userEvent.click(screen.getByText('outside'));
    expect(screen.getByRole('button', { expanded: false })).toBeInTheDocument();
  });

  it('moves focus between the live options with the arrows, wrapping', async () => {
    render(<Harness />);
    await userEvent.click(trigger());

    await userEvent.keyboard('{ArrowDown}');
    expect(screen.getByRole('option', { name: /Prem/ })).toHaveFocus();
    await userEvent.keyboard('{ArrowDown}');
    expect(screen.getByRole('option', { name: /Plain/ })).toHaveFocus();
    // The busy row is disabled and skipped; past the end the cursor wraps.
    await userEvent.keyboard('{ArrowDown}');
    expect(screen.getByRole('option', { name: /Prem/ })).toHaveFocus();
    await userEvent.keyboard('{ArrowUp}');
    expect(screen.getByRole('option', { name: /Plain/ })).toHaveFocus();
  });

  it('goes dead for the unpicked rows once the server cap is reached', async () => {
    const many = Array.from({ length: MAX_SEARCH_ACCOUNTS + 1 }, (_, index) => ({
      account_id: `acc-${String(index)}`,
      name: `Acc ${String(index)}`,
      premium: false,
      busy_reason: null,
    }));
    const onChange = vi.fn();
    render(
      <Harness
        accounts={many}
        initial={many.slice(0, MAX_SEARCH_ACCOUNTS).map((account) => account.account_id)}
        onChange={onChange}
      />,
    );
    await userEvent.click(trigger());

    const last = screen.getByRole('option', { name: `Acc ${String(MAX_SEARCH_ACCOUNTS)}` });
    expect(last).toBeDisabled();
    expect(
      screen.getByText(`максимум ${String(MAX_SEARCH_ACCOUNTS)} аккаунтов`),
    ).toBeInTheDocument();
    // A picked row stays live so the operator can free a slot.
    await userEvent.click(screen.getByRole('option', { name: 'Acc 0' }));
    expect(onChange).toHaveBeenLastCalledWith(
      many.slice(1, MAX_SEARCH_ACCOUNTS).map((a) => a.account_id),
    );
    expect(last).toBeEnabled();
    expect(screen.queryByText(/максимум/)).not.toBeInTheDocument();
  });

  it('says it is loading', () => {
    render(<Harness accounts={[]} initial={[]} loading />);
    expect(screen.getByText('Загружаем аккаунты…')).toBeInTheDocument();
    expect(screen.queryByText(/Нет свободных аккаунтов/)).not.toBeInTheDocument();
  });

  it('reports a failed load instead of an empty list', () => {
    render(<Harness accounts={[]} initial={[]} errored />);
    expect(screen.getByText(/Не удалось загрузить аккаунты/)).toBeInTheDocument();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('keeps a cached list through a failed refetch and says it is stale', () => {
    // TanStack keeps `data` when a refetch fails, and the modal still submits with those
    // accounts — so hiding the picker behind the danger notice would let «Найти» post a
    // list the operator can no longer see or change.
    render(<Harness errored />);
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Список аккаунтов не обновился');
    expect(screen.queryByText(/Не удалось загрузить аккаунты/)).not.toBeInTheDocument();
  });

  it('warns when no account is eligible', () => {
    render(<Harness accounts={[ACCOUNTS[2]!]} initial={[]} />);
    expect(screen.getByText(/Нет свободных аккаунтов/)).toBeInTheDocument();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('has no axe violations open or closed', async () => {
    const { container } = render(<Harness />);
    await expectNoAxeViolations(container);
    await userEvent.click(trigger());
    await expectNoAxeViolations(container);
  });
});
