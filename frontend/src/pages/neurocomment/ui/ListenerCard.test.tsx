import { render, screen } from '@testing-library/react';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import { ListenerCard } from './ListenerCard';

const OPTIONS: AccountRead[] = [
  { account_id: 'a1', status: 'alive', first_name: 'Alisa', created_at: 'now', updated_at: 'now' },
  { account_id: 'a2', status: 'alive', first_name: 'Boris', created_at: 'now', updated_at: 'now' },
];

// No listener set yet, so the card renders the account-picking dropdown. `listenerOpen`
// is owned by the page, so opening the list is a rerender, not a click.
function card(listenerOpen: boolean): ReactElement {
  return (
    <ListenerCard
      listenerId=""
      running={false}
      activeCampaignCount={0}
      activeChannelCount={0}
      unwatchedChannels={[]}
      listenerActionsOpen={false}
      onToggleActions={vi.fn()}
      onToggleRuntime={vi.fn()}
      onEdit={vi.fn()}
      onRemove={vi.fn()}
      listenerOpen={listenerOpen}
      onToggleOpen={vi.fn()}
      accountOptions={OPTIONS}
      onPickListener={vi.fn()}
    />
  );
}

// A listener IS set, so the card renders the status plaque instead of the dropdown.
function plaque(
  running: boolean,
  activeCampaignCount: number,
  activeChannelCount: number,
): ReactElement {
  return (
    <ListenerCard
      listenerId="a1"
      running={running}
      activeCampaignCount={activeCampaignCount}
      activeChannelCount={activeChannelCount}
      unwatchedChannels={[]}
      listenerActionsOpen={false}
      onToggleActions={vi.fn()}
      onToggleRuntime={vi.fn()}
      onEdit={vi.fn()}
      onRemove={vi.fn()}
      listenerOpen={false}
      onToggleOpen={vi.fn()}
      accountOptions={OPTIONS}
      onPickListener={vi.fn()}
    />
  );
}

test('a listener with campaigns still reads as plainly listening', () => {
  render(plaque(true, 2, 5));

  expect(screen.getByText('Слушает')).toBeVisible();
  expect(screen.queryByText(/каналов нет/)).toBeNull();
});

// The process is up but has no channel to listen to; the plaque used to keep promising
// green "Слушает" next to its own `0`, and an operator came asking which one was true.
test('a listener left with no campaign says so instead of promising work', () => {
  render(plaque(true, 0, 0));

  expect(screen.getByText('Слушает, каналов нет')).toBeVisible();
  expect(screen.queryByText('Слушает')).toBeNull();
});

test('a paused listener still reads as paused', () => {
  render(plaque(false, 0, 0));

  expect(screen.getByText('На паузе')).toBeVisible();
});

// .tb-dd collapses VISUALLY only (max-height:0 + opacity:0), so the account buttons
// are rendered and kept their tab stops while the list was closed. `inert` is what
// keeps a keyboard operator out; happy-dom honours it for focus, which is exactly the
// property under test.
test('a closed listener dropdown takes no focus, an open one does', () => {
  const view = render(card(false));

  const closed = screen.getByRole('button', { name: 'Boris' });
  closed.focus();
  expect(closed).not.toHaveFocus();

  view.rerender(card(true));
  const open = screen.getByRole('button', { name: 'Boris' });
  open.focus();
  expect(open).toHaveFocus();
});

// The state an audit found the first version of this plaque still lying in: the campaign is
// active, so a campaign-count test called it healthy, but its channels were freed one at a
// time and the watch set is empty. Same shape as the "up but deaf" state `_lifecycle`
// documents, where reconcile unsubscribes a listener whose account is warming.
test('an active campaign with no channels left does not buy a green plaque', () => {
  render(plaque(true, 1, 0));

  expect(screen.getByText('Слушает, каналов нет')).toBeVisible();
  expect(screen.queryByText('Слушает')).toBeNull();
});
