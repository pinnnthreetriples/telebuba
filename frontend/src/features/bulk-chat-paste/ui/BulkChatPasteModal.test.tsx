import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { BulkChatPasteModal } from './BulkChatPasteModal';

function setup(current: string[] = []) {
  const onAdd = vi.fn();
  const onClose = vi.fn();
  render(
    <BulkChatPasteModal current={current} allowFolders={false} onAdd={onAdd} onClose={onClose} />,
  );
  return { onAdd, onClose };
}

// `fireEvent`-fast paste: typing hundreds of links key by key is slow and proves nothing.
async function paste(text: string) {
  await userEvent.click(screen.getByLabelText('Список чатов'));
  await userEvent.paste(text);
}

test('the verdict sits in a live region so a screen reader hears it', async () => {
  setup();
  await paste('@alpha @beta');
  expect(screen.getByRole('status')).toHaveTextContent('Новых чатов: 2');
});

test('the textarea scrolls inside a capped pane, so the buttons stay reachable', () => {
  setup();
  const pane = screen.getByLabelText('Список чатов').parentElement;
  expect(pane?.className).toContain('max-h-feed');
  expect(pane?.className).toContain('overflow-y-auto');
});

test('a long list of bad links shows 30 of them, then the rest as a count', async () => {
  setup();
  const bad = Array.from({ length: 35 }, (_, index) => `!!bad${String(index)}`);
  await paste(bad.join('\n'));
  const notice = screen.getByText(/^Не распознаны, не добавятся \(35\)/);
  expect(notice).toHaveTextContent('!!bad29');
  expect(notice).not.toHaveTextContent('!!bad30');
  expect(notice).toHaveTextContent('и ещё 5');
});

test('Escape on an empty box closes it at once', async () => {
  const { onClose } = setup();
  await userEvent.keyboard('{Escape}');
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('Escape on a filled box does not close until confirmed', async () => {
  const { onClose } = setup();
  await paste('@alpha');
  await userEvent.keyboard('{Escape}');
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByText('Закрыть без сохранения?')).toBeInTheDocument();
});

test('the add button is disabled and says why when nothing is new', async () => {
  const { onAdd } = setup(['@alpha']);
  await paste('@Alpha');
  expect(screen.getByRole('button', { name: 'Нечего добавлять' })).toBeDisabled();
  expect(onAdd).not.toHaveBeenCalled();
});
