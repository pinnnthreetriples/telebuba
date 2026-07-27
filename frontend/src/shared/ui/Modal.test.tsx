import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';

import { Modal } from './Modal';

afterEach(() => {
  document.body.style.overflow = '';
});

test('backdrop click closes, card click does not, Escape closes', async () => {
  const onClose = vi.fn();
  render(
    <Modal onClose={onClose}>
      <div>содержимое</div>
    </Modal>,
  );

  // card click does NOT close
  await userEvent.click(screen.getByText('содержимое'));
  expect(onClose).not.toHaveBeenCalled();

  // backdrop click closes
  await userEvent.click(screen.getByRole('presentation'));
  expect(onClose).toHaveBeenCalledTimes(1);

  // Escape closes
  await userEvent.keyboard('{Escape}');
  expect(onClose).toHaveBeenCalledTimes(2);
});

test('Escape only closes the topmost modal, not the parent underneath it', async () => {
  const onCloseParent = vi.fn();
  const onCloseChild = vi.fn();
  render(
    <>
      <Modal onClose={onCloseParent} z={70}>
        <div>родитель</div>
      </Modal>
      <Modal onClose={onCloseChild} z={80}>
        <div>потомок</div>
      </Modal>
    </>,
  );
  await userEvent.keyboard('{Escape}');
  // Only the last-mounted (topmost) modal handles the key.
  expect(onCloseChild).toHaveBeenCalledTimes(1);
  expect(onCloseParent).not.toHaveBeenCalled();
});

test('locks page scroll while any dialog is open and restores it when the last one closes', () => {
  const { unmount } = render(
    <Modal onClose={vi.fn()}>
      <div>один</div>
    </Modal>,
  );
  expect(document.body.style.overflow).toBe('hidden');
  unmount();
  expect(document.body.style.overflow).toBe('');
});

// A nested dialog captures body.overflow *after* the outer one already set it to
// 'hidden'. React runs deletion cleanups parent-first, so when both unmount in one
// commit the nested one runs LAST — if each instance restored its own snapshot, that
// snapshot is 'hidden' and the page could never be scrolled again without a reload.
// Reachable for real via ProfileModal's "discard changes" confirm, whose onConfirm
// closes the parent.
test('a nested dialog closing with its parent still unlocks page scroll', () => {
  function Pair({ open }: { open: boolean }) {
    if (!open) return null;
    return (
      <>
        <Modal onClose={vi.fn()} z={70}>
          <div>родитель</div>
        </Modal>
        <Modal onClose={vi.fn()} z={80}>
          <div>потомок</div>
        </Modal>
      </>
    );
  }
  const { rerender } = render(<Pair open />);
  expect(document.body.style.overflow).toBe('hidden');

  rerender(<Pair open={false} />);
  expect(document.body.style.overflow).toBe('');
});

test('focuses the dialog on open and restores focus to the opener on close', () => {
  const opener = document.createElement('button');
  document.body.appendChild(opener);
  opener.focus();
  const { unmount } = render(
    <Modal onClose={vi.fn()}>
      <button type="button">внутри</button>
    </Modal>,
  );
  expect(screen.getByRole('dialog')).toHaveFocus();
  unmount();
  expect(opener).toHaveFocus();
  opener.remove();
});

test('Tab is trapped inside the dialog and wraps around', async () => {
  render(
    <Modal onClose={vi.fn()}>
      <button type="button">один</button>
      <button type="button">два</button>
    </Modal>,
  );
  // Tab from the last focusable wraps to the first.
  screen.getByText('два').focus();
  await userEvent.tab();
  expect(screen.getByText('один')).toHaveFocus();
  // Shift+Tab from the first wraps back to the last.
  await userEvent.tab({ shift: true });
  expect(screen.getByText('два')).toHaveFocus();
});
