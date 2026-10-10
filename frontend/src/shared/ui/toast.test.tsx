import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { layer } from '@/shared/design-system/tokens';

import { expectNoAxeViolations } from './axe.test-helpers';
import { Modal } from './Modal';
import { Toaster } from './Toaster';
import { toastError, toastSuccess } from './toast';

import '@/shared/i18n';

const zIndex: Record<string, string> = layer;

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
test('a toast raised before a dialog opens still sits above it', async () => {
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

  // The axe pass lives in this test rather than the one above it because axe cannot
  // run on a frozen clock, and this is the test that never advances it. The stack
  // portals to document.body, so the stack's own element is the root.
  vi.useRealTimers();
  await expectNoAxeViolations(toastLayer as Element);
});

test('a success toast carries an undo that runs once and dismisses the toast', () => {
  render(<Toaster />);
  const undo = vi.fn();
  act(() => {
    toastSuccess('2 аккаунта → «Основные»', { label: 'Отменить', onClick: undo });
  });
  const toast = screen.getByText('2 аккаунта → «Основные»').closest('[role="status"]')!;
  expect(toast).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Отменить' }));
  expect(undo).toHaveBeenCalledTimes(1);
  expect(screen.queryByText('2 аккаунта → «Основные»')).not.toBeInTheDocument();
});

test('a success toast closes by hand and on its own after 5 s', () => {
  render(<Toaster />);
  act(() => {
    toastSuccess('Папка создана');
  });
  expect(screen.queryByRole('button', { name: 'Отменить' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Скрыть' }));
  expect(screen.queryByText('Папка создана')).not.toBeInTheDocument();

  act(() => {
    toastSuccess('Папка удалена');
  });
  expect(screen.getByText('Папка удалена')).toBeInTheDocument();
  act(() => {
    vi.advanceTimersByTime(5000);
  });
  expect(screen.queryByText('Папка удалена')).not.toBeInTheDocument();
});

// A burst piles up as one stack: the newest in front, older ones peeking above it,
// smaller, and beyond three hidden — not a column of cards over the dialog.
test('toasts overlap as a pile, newest in front', () => {
  render(<Toaster />);
  act(() => {
    for (let i = 0; i < 4; i++) toastError(`Ошибка ${i}`);
  });
  // From the end: an earlier test leaves its toast queued on a frozen clock.
  const [oldest, , older, newest] = screen.getAllByRole('alert', { hidden: true }).slice(-4);
  expect(newest!.style.translate).toBe('-50% 0px');
  expect(older!.style.translate).toBe('-50% -10px');
  expect(Number(older!.style.zIndex)).toBeLessThan(Number(newest!.style.zIndex));
  expect(oldest).toHaveStyle({ visibility: 'hidden' });
  act(() => {
    vi.advanceTimersByTime(5000);
  });
});
