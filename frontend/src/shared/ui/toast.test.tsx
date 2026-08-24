import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import config from '../../../tailwind.config';

import { Modal } from './Modal';
import { Toaster } from './Toaster';
import { toastError } from './toast';

const zIndex = config.theme?.zIndex as Record<string, string>;

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

// Toasts must clear an open dialog, and the case that decides it is the one where the
// toast is already on screen when the dialog opens: the dialog's portal is appended to
// body after the toast's, so at an equal z-index it would win the tie and paint over
// it. Naming the rung is what makes the outcome independent of that order.
test('a toast raised before a dialog opens still sits above it', () => {
  render(<Toaster />);
  act(() => {
    toastError('Something broke');
  });
  render(
    <Modal onClose={() => {}} label="Dialog">
      body
    </Modal>,
  );

  const toastLayer = screen.getByRole('alert').parentElement;
  const dialogLayer = screen.getByRole('dialog').parentElement;
  expect(toastLayer).toHaveClass('z-toast');
  expect(dialogLayer).toHaveClass('z-dialog');
  expect(Number(zIndex.toast)).toBeGreaterThan(Number(zIndex.dialog));
});
