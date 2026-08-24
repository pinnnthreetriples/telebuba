import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import { ChannelStatusBadge } from './ChannelStatusBadge';

test('renders the localized channel status', () => {
  render(<ChannelStatusBadge status="ready" />);
  expect(screen.getByText('Готов')).toBeInTheDocument();
});

test('uses the danger tone for chat_restricted', () => {
  render(<ChannelStatusBadge status="chat_restricted" />);
  // Tone comes from the token pair, not an inline hex.
  expect(screen.getByText('Чат ограничен')).toHaveClass('text-danger-deep', 'bg-danger-tint');
});

test('renders banned in the danger tone', () => {
  render(<ChannelStatusBadge status="banned" />);
  expect(screen.getByText('Забанен')).toHaveClass('text-danger-deep');
});

test('a kicked pair getting itself back in is amber, not the red join failure', () => {
  render(<ChannelStatusBadge status="rejoining" />);
  expect(screen.getByText('Возвращаемся в чат')).toHaveClass('text-warning-deep');
});
