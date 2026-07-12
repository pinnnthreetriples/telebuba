import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { Modal } from './Modal';

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
