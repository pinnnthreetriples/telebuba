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
    <Modal onClose={onClose} label="диалог">
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
      <Modal onClose={onCloseParent} label="родитель">
        <div>родитель</div>
      </Modal>
      <Modal onClose={onCloseChild} label="потомок">
        <div>потомок</div>
      </Modal>
    </>,
  );
  await userEvent.keyboard('{Escape}');
  // Only the last-mounted (topmost) modal handles the key.
  expect(onCloseChild).toHaveBeenCalledTimes(1);
  expect(onCloseParent).not.toHaveBeenCalled();
});

// A dialog taller than the viewport has to stay reachable. The overlay is `fixed` and
// body scroll is locked, so if the overlay does not scroll, the card's overflowing top
// and bottom cannot be reached by any means — and it must be the OVERLAY that scrolls,
// not the card, or the card clips absolutely-positioned children meant to escape it.
test('a too-tall dialog scrolls via the overlay, and the card does not clip', () => {
  render(
    <Modal onClose={vi.fn()} label="диалог">
      <div>высокое содержимое</div>
    </Modal>,
  );

  const overlay = screen.getByRole('presentation');
  expect(overlay).toHaveClass('overflow-y-auto');
  // m-auto, not items-center: centring with align-items makes the overflowing top
  // unreachable once the container scrolls.
  const card = screen.getByRole('dialog');
  expect(card).toHaveClass('m-auto');
  expect(overlay).not.toHaveClass('items-center');
  expect(card.className).not.toContain('overflow-y-auto');
});

test('locks page scroll while any dialog is open and restores it when the last one closes', () => {
  const { unmount } = render(
    <Modal onClose={vi.fn()} label="диалог">
      <div>один</div>
    </Modal>,
  );
  expect(document.body.style.overflow).toBe('hidden');
  unmount();
  expect(document.body.style.overflow).toBe('');
});

// A second dialog captures body.overflow *after* the first already set it to 'hidden'.
// Cleanup order follows document order, so the later sibling restores LAST — and if
// each instance restored its own snapshot, that snapshot is 'hidden' and the page could
// never be scrolled again without a reload. This is the real shape of ProfileModal's
// "discard changes" confirm: a sibling of the dialog in the same fragment, whose
// onConfirm closes the parent, so both unmount together.
test('a second dialog closing with the first still unlocks page scroll', () => {
  function Pair({ open }: { open: boolean }) {
    if (!open) return null;
    return (
      <>
        <Modal onClose={vi.fn()} label="родитель">
          <div>родитель</div>
        </Modal>
        <Modal onClose={vi.fn()} label="потомок">
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
    <Modal onClose={vi.fn()} label="диалог">
      <button type="button">внутри</button>
    </Modal>,
  );
  expect(screen.getByRole('dialog')).toHaveFocus();
  unmount();
  expect(opener).toHaveFocus();
  opener.remove();
});

// `label` is required, not optional: while it was optional 20 of the 21 call sites
// left it out and a screen reader announced a nameless "dialog". This is the whole
// contract — getByRole('dialog', { name }) resolves only through the aria-label.
test('the dialog carries its accessible name', () => {
  render(
    <Modal onClose={vi.fn()} label="Настройки прогрева">
      <div>содержимое</div>
    </Modal>,
  );

  expect(screen.getByRole('dialog', { name: 'Настройки прогрева' })).toBeInTheDocument();
  expect(screen.getByRole('dialog')).toHaveAttribute('aria-modal', 'true');
});

test('Tab is trapped inside the dialog and wraps around', async () => {
  render(
    <Modal onClose={vi.fn()} label="диалог">
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

// A closed .tb-dd dropdown is inert, and its options still match the focusable
// selector. One at the END of a dialog's focusable list made the wrap target an
// element whose .focus() the browser ignores — and since the trap has already
// called preventDefault, Shift+Tab would then go nowhere at all.
test('the Tab trap wraps past an inert element instead of freezing on it', async () => {
  render(
    <Modal onClose={vi.fn()} label="диалог">
      <button type="button">один</button>
      <button type="button">два</button>
      <div inert>
        <button type="button">закрытый</button>
      </div>
    </Modal>,
  );

  screen.getByText('один').focus();
  await userEvent.tab({ shift: true });
  expect(screen.getByText('два')).toHaveFocus();
});
