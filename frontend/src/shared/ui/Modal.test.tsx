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
