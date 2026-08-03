import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { ListenerEditModal } from './ListenerEditModal';

const OPTIONS = [
  { id: 'a1', name: 'Ivan Petrov' },
  { id: 'a2', name: 'Maria Sidorova' },
];

test('opens the dropdown, picks an option, saves with swap and closes', async () => {
  const onClose = vi.fn();
  const onSave = vi.fn();
  render(<ListenerEditModal options={OPTIONS} selected={null} onClose={onClose} onSave={onSave} />);
  expect(screen.getByText('Аккаунт-слушатель')).toBeInTheDocument();

  // open the custom dropdown and pick the second option
  await userEvent.click(screen.getByText('Выберите аккаунт…'));
  await userEvent.click(screen.getByText('Maria Sidorova'));

  await userEvent.click(screen.getByText('Сохранить'));
  expect(onSave).toHaveBeenCalledWith('a2');
  expect(screen.getByText('Сохранено')).toBeInTheDocument();
  await waitFor(() => {
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

// .tb-dd collapses VISUALLY only (max-height:0 + opacity:0), so the options are
// rendered whether the list is open or not — `inert` is the only thing keeping a
// keyboard operator out of a closed one. happy-dom honours inert for focus, which
// is exactly the property under test (it does not filter the a11y tree, so the
// option is still findable — that is the limitation, not the app's behaviour).
test('a closed dropdown takes no focus, an open one does', async () => {
  render(
    <ListenerEditModal options={OPTIONS} selected={null} onClose={vi.fn()} onSave={vi.fn()} />,
  );

  const closed = screen.getByRole('button', { name: 'Maria Sidorova' });
  closed.focus();
  expect(closed).not.toHaveFocus();

  await userEvent.click(screen.getByText('Выберите аккаунт…'));
  const open = screen.getByRole('button', { name: 'Maria Sidorova' });
  open.focus();
  expect(open).toHaveFocus();
});

test('cancel closes without saving', async () => {
  const onClose = vi.fn();
  const onSave = vi.fn();
  render(<ListenerEditModal options={OPTIONS} selected="a1" onClose={onClose} onSave={onSave} />);
  await userEvent.click(screen.getByLabelText('Закрыть'));
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(onSave).not.toHaveBeenCalled();
});
