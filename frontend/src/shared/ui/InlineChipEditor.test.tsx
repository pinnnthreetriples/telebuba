import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, test, vi } from 'vitest';

import { InlineChipEditor } from './InlineChipEditor';
import { Modal } from './Modal';

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

test('Escape inside a dialog cancels the editor only, not the dialog around it', async () => {
  const user = userEvent.setup();
  const onCancel = vi.fn();
  const onClose = vi.fn();
  render(
    <Modal onClose={onClose} label="Диалог">
      <Example onConfirm={vi.fn()} onCancel={onCancel} />
    </Modal>,
  );

  await user.type(screen.getByRole('textbox', { name: 'Новый канал' }), '{Escape}');
  expect(onCancel).toHaveBeenCalledTimes(1);
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByRole('dialog', { name: 'Диалог' })).toBeInTheDocument();
});
