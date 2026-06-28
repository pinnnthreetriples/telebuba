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
