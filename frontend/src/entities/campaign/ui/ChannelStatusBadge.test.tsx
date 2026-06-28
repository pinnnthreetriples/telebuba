import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import { ChannelStatusBadge } from './ChannelStatusBadge';

test('renders the localized channel status', () => {
  render(<ChannelStatusBadge status="ready" />);
  expect(screen.getByText('Готов')).toBeInTheDocument();
});

test('uses the danger colour for chat_restricted', () => {
  render(<ChannelStatusBadge status="chat_restricted" />);
  expect(screen.getByText('Чат ограничен').className).toContain('text-danger');
});
