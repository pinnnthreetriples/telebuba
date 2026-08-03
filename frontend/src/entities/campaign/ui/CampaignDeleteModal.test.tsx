import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { CampaignDeleteModal } from './CampaignDeleteModal';

test('confirm fires onConfirm then onClose, cancel only closes', async () => {
  const onClose = vi.fn();
  const onConfirm = vi.fn();
  const { rerender } = render(
    <CampaignDeleteModal name="Крипто" onClose={onClose} onConfirm={onConfirm} />,
  );
  expect(screen.getByText('Удалить кампанию «Крипто»?')).toBeInTheDocument();
  // The dialog's own name has to name the campaign too: it is all a screen reader
  // announces on open, and "Удалить кампанию?" would not say which one.
  expect(screen.getByRole('dialog', { name: 'Удалить кампанию «Крипто»?' })).toBeInTheDocument();

  await userEvent.click(screen.getByText('Удалить'));
  expect(onConfirm).toHaveBeenCalledTimes(1);
  expect(onClose).toHaveBeenCalledTimes(1);

  rerender(<CampaignDeleteModal name="Крипто" onClose={onClose} onConfirm={onConfirm} />);
  await userEvent.click(screen.getByText('Отмена'));
  expect(onClose).toHaveBeenCalledTimes(2);
});
