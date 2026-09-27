import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { SelectableCard } from './SelectableCard';

test('selection and action toggle are separate keyboard-accessible buttons', async () => {
  const onSelect = vi.fn();
  const onToggleActions = vi.fn();
  render(
    <SelectableCard
      surfaceId="campaign-example"
      name="Крипта"
      meta="4 канала · 3 аккаунта"
      status={<span>Активна</span>}
      selected={false}
      actionsOpen={false}
      actionsLabel="Действия"
      actions={<button type="button">Удалить</button>}
      onSelect={onSelect}
      onToggleActions={onToggleActions}
    />,
  );

  const user = userEvent.setup();
  const select = screen.getByRole('button', { name: 'Крипта' });
  select.focus();
  await user.keyboard('{Enter}');
  expect(onSelect).toHaveBeenCalledTimes(1);

  await user.click(screen.getByRole('button', { name: 'Действия' }));
  expect(onToggleActions).toHaveBeenCalledTimes(1);
  expect(onSelect).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: 'Действия' })).toHaveAttribute(
    'aria-controls',
    'campaign-example',
  );
});
