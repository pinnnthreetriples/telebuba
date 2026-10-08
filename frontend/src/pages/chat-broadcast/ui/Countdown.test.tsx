import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { Countdown } from './Countdown';

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-10-06T12:00:00Z'));
});
afterEach(() => {
  vi.useRealTimers();
});

test('shows minutes and padded seconds', () => {
  render(<Countdown to={Date.parse('2026-10-06T12:02:05Z')} />);
  expect(screen.getByText('через 2:05')).toBeInTheDocument();
});

test('ticks every second and says "sending" when the time is up', () => {
  render(<Countdown to={Date.parse('2026-10-06T12:00:03Z')} />);
  expect(screen.getByText('через 0:03')).toBeInTheDocument();

  act(() => {
    vi.advanceTimersByTime(1000);
  });
  expect(screen.getByText('через 0:02')).toBeInTheDocument();

  act(() => {
    vi.advanceTimersByTime(2000);
  });
  expect(screen.getByText('отправляет…')).toBeInTheDocument();
});
