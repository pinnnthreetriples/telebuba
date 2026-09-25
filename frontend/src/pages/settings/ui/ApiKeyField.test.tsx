import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { ApiKeyField } from './ApiKeyField';

test('API key visibility action calls its toggle handler', async () => {
  const user = userEvent.setup();
  const onToggleShow = vi.fn();

  render(
    <ApiKeyField
      label="API key"
      value="secret"
      show={false}
      keySet
      placeholder="Enter key"
      toggleLabel="Show API key"
      clearLabel="Clear API key"
      onChange={vi.fn()}
      onToggleShow={onToggleShow}
      onClear={vi.fn()}
    />,
  );

  expect(screen.getByPlaceholderText('Enter key')).toHaveAttribute('type', 'password');
  const toggle = screen.getByRole('button', { name: 'Show API key' });
  expect(toggle).toHaveClass('focus-visible:outline-focus', 'h-control', 'w-action');
  await user.click(toggle);
  expect(onToggleShow).toHaveBeenCalledOnce();
});
