import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import { StatusBadge } from './StatusBadge';

test('renders the localized status label', () => {
  render(<StatusBadge status="alive" />);
  expect(screen.getByText('Активен')).toBeInTheDocument();
});

// The dot is the account pill's whole shape in the design; losing it silently is
// the one thing that survives a colour assertion.
test('the account pill leads with its dot', () => {
  render(<StatusBadge status="alive" />);
  expect(screen.getByText('Активен').querySelector('.bg-current')).toBeInTheDocument();
});

// `text-info-strong`, and the exact rung matters. This used to assert `text-primary`
// while the badge painted `text-primary-deep`, and it passed — `toContain` is a substring
// check and one name was a prefix of the other. The semantic rename broke the accident and
// nothing else, which is the argument for naming the rung a component actually paints.
test('uses the design needs-code colour for unauthorized', () => {
  render(<StatusBadge status="unauthorized" />);
  const classes = screen.getByText('Не авторизован').className;
  expect(classes).toContain('text-info-strong');
  expect(classes).toContain('bg-info-tint');
});

test('uses the design banned colour for a permanent-failure status', () => {
  render(<StatusBadge status="session_error" />);
  expect(screen.getByText('Ошибка сессии').className).toContain('text-danger');
});

test('renders frozen with the localized label and banned colour', () => {
  render(<StatusBadge status="frozen" />);
  expect(screen.getByText('Заморожен').className).toContain('text-danger');
});
