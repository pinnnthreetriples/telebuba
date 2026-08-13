import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
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

// The card hosts CommentModeToggle, which reads /settings itself, so the tree needs a
// query client. It is inside `card()` (not a renderWithClient wrapper) because the test
// below rerenders — a wrapper applied once would be dropped by the second render.
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

// No listener set yet, so the card renders the account-picking dropdown. `listenerOpen`
// is owned by the page, so opening the list is a rerender, not a click.
function card(listenerOpen: boolean): ReactElement {
  return (
    <QueryClientProvider client={queryClient}>
      <ListenerCard
        listenerId=""
        running={false}
        activeCampaignCount={0}
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
    </QueryClientProvider>
  );
}

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
