import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import { WarmingStateBadge } from './WarmingStateBadge';

test('renders the localized warming state', () => {
  render(<WarmingStateBadge state="active" />);
  expect(screen.getByText('Активен')).toBeInTheDocument();
});

test('uses the danger colour for the error state', () => {
  render(<WarmingStateBadge state="error" />);
  expect(screen.getByText('Ошибка').className).toContain('text-danger');
});
