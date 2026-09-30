import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { ChipAddButton } from './ChipAddButton';

test('is the muted dashed md pill and forwards clicks and disabled', async () => {
  const onClick = vi.fn();
  const { rerender } = render(<ChipAddButton onClick={onClick}>+ Канал</ChipAddButton>);
  const button = screen.getByRole('button', { name: '+ Канал' });
  expect(button.className).toContain('border-dashed');
  expect(button.className).toContain('border-line-strong');
  expect(button.className).toContain('font-medium');
  await userEvent.setup().click(button);
  expect(onClick).toHaveBeenCalledTimes(1);

  rerender(
    <ChipAddButton disabled onClick={onClick}>
      + Канал
    </ChipAddButton>,
  );
  expect(screen.getByRole('button', { name: '+ Канал' })).toBeDisabled();
});
