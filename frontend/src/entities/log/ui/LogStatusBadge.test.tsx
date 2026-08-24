import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import { LogStatusBadge } from './LogStatusBadge';

test('renders the localized log status', () => {
  render(<LogStatusBadge status="success" />);
  expect(screen.getByText('Успех')).toBeInTheDocument();
});

test('uses the danger colour for error', () => {
  render(<LogStatusBadge status="error" />);
  expect(screen.getByText('Ошибка').className).toContain('text-danger');
});

// A log column is already a run of severities read top to bottom; a dot on every
// row of it is a column of dots, so this is the one status pill without one.
test('the level pill carries no dot', () => {
  render(<LogStatusBadge status="warning" />);
  expect(screen.getByText('Предупреждение').querySelector('.bg-current')).toBeNull();
});
