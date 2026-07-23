import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import { StatusBadge } from './StatusBadge';

test('renders the localized status label', () => {
  render(<StatusBadge status="alive" />);
  expect(screen.getByText('Активен')).toBeInTheDocument();
});

test('uses the design needs-code colour for unauthorized', () => {
  render(<StatusBadge status="unauthorized" />);
  expect(screen.getByText('Не авторизован').className).toContain('text-[#0066ff]');
});

test('uses the design banned colour for a permanent-failure status', () => {
  render(<StatusBadge status="session_error" />);
  expect(screen.getByText('Ошибка сессии').className).toContain('text-[#e5372a]');
});

test('renders frozen with the localized label and banned colour', () => {
  render(<StatusBadge status="frozen" />);
  expect(screen.getByText('Заморожен').className).toContain('text-[#e5372a]');
});
