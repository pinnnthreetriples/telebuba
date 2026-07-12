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
  // Design dot-pill: colour is an inline hex, not a token class.
  expect(screen.getByText('Чат ограничен')).toHaveStyle({ color: '#c0473f' });
});

test('renders banned in the danger colour', () => {
  render(<ChannelStatusBadge status="banned" />);
  expect(screen.getByText('Забанен')).toHaveStyle({ color: '#c0473f' });
});
