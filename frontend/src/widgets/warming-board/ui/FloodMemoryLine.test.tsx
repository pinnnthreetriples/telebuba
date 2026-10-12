import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import { FloodMemoryLine } from './FloodMemoryLine';

test('shows floods, the DM block and the ramped limit in one line', () => {
  render(
    <FloodMemoryLine
      account={{ flood_total: 2, dm_blocked_until: '2026-10-25T12:00:00+00:00', limit_factor: 0.6 }}
    />,
  );
  expect(
    screen.getByText(/^Флудов всего: 2 · ЛС закрыты до 25 окт\.? · лимит 60\s?%$/),
  ).toBeVisible();
});

test('shows only the parts that still say something', () => {
  render(<FloodMemoryLine account={{ flood_total: 1, dm_blocked_until: null, limit_factor: 1 }} />);
  expect(screen.getByText('Флудов всего: 1')).toBeVisible();
});

test('renders nothing for an account that never met a flood', () => {
  const { container } = render(
    <FloodMemoryLine account={{ flood_total: 0, dm_blocked_until: null, limit_factor: 1 }} />,
  );
  expect(container).toBeEmptyDOMElement();
});

test('skips an unreadable DM date', () => {
  render(
    <FloodMemoryLine account={{ flood_total: 3, dm_blocked_until: 'nope', limit_factor: 0.3 }} />,
  );
  expect(screen.getByText(/^Флудов всего: 3 · лимит 30\s?%$/)).toBeVisible();
});
