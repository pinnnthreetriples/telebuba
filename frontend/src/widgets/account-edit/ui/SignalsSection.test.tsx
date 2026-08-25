import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import { SignalsSection } from './SignalsSection';

const ACCOUNT: AccountRead = {
  account_id: 'a1',
  status: 'alive',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

// `.tb-tip-pop` opens on `:hover` and `:focus-within` (the stylesheet's half is asserted
// in src/app/styles/index.test.ts — happy-dom applies no CSS, so the reveal itself cannot
// be seen from here). The spam-check button was already a tab stop, so what was missing
// was only the description: an unnamed `.tb-tip-pop` is a sibling <span> of text to a
// screen reader, revealed or not.
test('the spam-check button names the tooltip that explains it', () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <SignalsSection account={ACCOUNT} />
    </QueryClientProvider>,
  );

  const button = screen.getByRole('button', { name: 'Проверить' });
  button.focus();
  expect(button).toHaveFocus();

  const tip = document.getElementById(button.getAttribute('aria-describedby') ?? '');
  expect(tip).toHaveAttribute('role', 'tooltip');
  expect(tip).toHaveTextContent('Проверка на @SpamBot');
});
