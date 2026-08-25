import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
import { Switch } from './Switch';

test('reports its state through role/aria and toggles to the opposite value', async () => {
  const onChange = vi.fn();
  const { container, rerender } = render(
    <Switch checked={false} onChange={onChange} label="Резерв" />,
  );

  const control = screen.getByRole('switch', { name: 'Резерв' });
  expect(control).toHaveAttribute('aria-checked', 'false');
  await expectNoAxeViolations(container);

  await userEvent.click(control);
  expect(onChange).toHaveBeenCalledWith(true);

  rerender(<Switch checked onChange={onChange} label="Резерв" />);
  expect(screen.getByRole('switch', { name: 'Резерв' })).toHaveAttribute('aria-checked', 'true');
  await userEvent.click(screen.getByRole('switch', { name: 'Резерв' }));
  expect(onChange).toHaveBeenLastCalledWith(false);
});

test('a disabled switch is inert, so a not-yet-wired feature cannot be toggled', async () => {
  const onChange = vi.fn();
  render(<Switch disabled checked={false} onChange={onChange} label="Отвечать людям" />);

  const control = screen.getByRole('switch', { name: 'Отвечать людям' });
  expect(control).toBeDisabled();
  await userEvent.click(control);
  expect(onChange).not.toHaveBeenCalled();
});
