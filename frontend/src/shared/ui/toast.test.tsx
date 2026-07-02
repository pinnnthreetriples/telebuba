import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { Toaster } from './Toaster';
import { toastError } from './toast';

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

test('renders a queued error message and auto-dismisses it', () => {
  render(<Toaster />);
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();

  act(() => {
    toastError('Something broke');
  });
  expect(screen.getByRole('alert')).toHaveTextContent('Something broke');

  act(() => {
    vi.advanceTimersByTime(5000);
  });
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
