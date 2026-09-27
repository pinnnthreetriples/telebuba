import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, test, vi } from 'vitest';

import { InlineChipEditor } from './InlineChipEditor';

function Example({ onConfirm, onCancel }: { onConfirm: () => void; onCancel: () => void }) {
  const [value, setValue] = useState('');
  return (
    <InlineChipEditor
      value={value}
      onChange={setValue}
      onConfirm={onConfirm}
      onCancel={onCancel}
      placeholder="Канал"
      inputLabel="Новый канал"
      confirmLabel="Добавить"
      cancelLabel="Отмена"
    />
  );
}

test('confirms a nonempty value by button or Enter and cancels by Escape', async () => {
  const user = userEvent.setup();
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(<Example onConfirm={onConfirm} onCancel={onCancel} />);

  const input = screen.getByRole('textbox', { name: 'Новый канал' });
  const confirm = screen.getByRole('button', { name: 'Добавить' });
  expect(confirm).toBeDisabled();
  await user.type(input, '@channel{Enter}');
  expect(onConfirm).toHaveBeenCalledTimes(1);
  expect(confirm).toBeEnabled();
  await user.click(confirm);
  expect(onConfirm).toHaveBeenCalledTimes(2);
  await user.type(input, '{Escape}');
  expect(onCancel).toHaveBeenCalledTimes(1);
});
