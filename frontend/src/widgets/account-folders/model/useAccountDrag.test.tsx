import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import { DragGhost } from '../ui/DragGhost';
import { RowPick } from '../ui/RowPick';
import { useAccountDrag } from './useAccountDrag';

const ACCOUNTS: AccountRead[] = ['a', 'b', 'c', 'd', 'e', 'f'].map((id) => ({
  account_id: id,
  first_name: id.toUpperCase(),
  status: 'alive',
  created_at: 'now',
  updated_at: 'now',
}));

function Harness({
  selected,
  onDrop,
}: {
  selected: ReadonlySet<string>;
  onDrop: (folderId: string, ids: string[]) => void;
}) {
  const { drag, startDrag } = useAccountDrag(onDrop);
  return (
    <>
      <button type="button" data-folder-drop="f-main">
        Основные
      </button>
      <span>elsewhere</span>
      {ACCOUNTS.map((account) => (
        <RowPick
          key={account.account_id}
          name={account.account_id}
          selected={selected.has(account.account_id)}
          onToggle={() => undefined}
          onGripDown={(event) => {
            startDrag(event, account.account_id, selected);
          }}
        />
      ))}
      {drag ? (
        <DragGhost drag={drag} accounts={ACCOUNTS} folderName={drag.folderId ? 'Основные' : null} />
      ) : null}
    </>
  );
}

// happy-dom does no layout, so the element under the pointer is whatever the test says.
function pointAt(element: Element) {
  vi.spyOn(document, 'elementFromPoint').mockReturnValue(element);
}

function grip(id: string) {
  return screen.getByRole('button', { name: `Перетащить ${id} в папку` });
}

afterEach(() => {
  vi.restoreAllMocks();
});

test('dragging a selected row onto a folder carries the whole selection', () => {
  const onDrop = vi.fn();
  render(<Harness selected={new Set(['a', 'b', 'c', 'd', 'e'])} onDrop={onDrop} />);

  fireEvent.pointerDown(grip('b'), { button: 0, pointerId: 1, clientX: 10, clientY: 10 });
  pointAt(screen.getByText('elsewhere'));
  fireEvent.pointerMove(document, { pointerId: 1, clientX: 40, clientY: 40 });
  const ghost = screen.getByTestId('drag-ghost');
  expect(ghost).toHaveTextContent('+1');
  expect(ghost).not.toHaveTextContent('Основные');

  pointAt(screen.getByRole('button', { name: 'Основные' }));
  fireEvent.pointerMove(document, { pointerId: 1, clientX: 50, clientY: 5 });
  expect(screen.getByTestId('drag-ghost')).toHaveTextContent('Основные');

  fireEvent.pointerUp(document, { pointerId: 1, clientX: 50, clientY: 5 });
  expect(onDrop).toHaveBeenCalledWith('f-main', ['a', 'b', 'c', 'd', 'e']);
  expect(screen.queryByTestId('drag-ghost')).not.toBeInTheDocument();
});

test('an unselected row is dragged alone', () => {
  const onDrop = vi.fn();
  render(<Harness selected={new Set(['a'])} onDrop={onDrop} />);
  fireEvent.pointerDown(grip('f'), { button: 0, pointerId: 2, clientX: 0, clientY: 0 });
  pointAt(screen.getByRole('button', { name: 'Основные' }));
  fireEvent.pointerMove(document, { pointerId: 2, clientX: 30, clientY: 0 });
  fireEvent.pointerUp(document, { pointerId: 2 });
  expect(onDrop).toHaveBeenCalledWith('f-main', ['f']);
});

test('Escape cancels, and a drop outside a folder or a mere click drops nothing', () => {
  const onDrop = vi.fn();
  render(<Harness selected={new Set()} onDrop={onDrop} />);

  fireEvent.pointerDown(grip('a'), { button: 0, pointerId: 3, clientX: 0, clientY: 0 });
  pointAt(screen.getByRole('button', { name: 'Основные' }));
  fireEvent.pointerMove(document, { pointerId: 3, clientX: 30, clientY: 0 });
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(screen.queryByTestId('drag-ghost')).not.toBeInTheDocument();
  fireEvent.pointerUp(document, { pointerId: 3 });

  fireEvent.pointerDown(grip('a'), { button: 0, pointerId: 4, clientX: 0, clientY: 0 });
  pointAt(screen.getByText('elsewhere'));
  fireEvent.pointerMove(document, { pointerId: 4, clientX: 30, clientY: 0 });
  fireEvent.pointerUp(document, { pointerId: 4 });

  fireEvent.pointerDown(grip('a'), { button: 0, pointerId: 5, clientX: 0, clientY: 0 });
  pointAt(screen.getByRole('button', { name: 'Основные' }));
  fireEvent.pointerMove(document, { pointerId: 5, clientX: 2, clientY: 2 });
  fireEvent.pointerUp(document, { pointerId: 5 });

  expect(onDrop).not.toHaveBeenCalled();
});

test('a right-button press on the grip does not start a drag', () => {
  const onDrop = vi.fn();
  render(<Harness selected={new Set()} onDrop={onDrop} />);
  fireEvent.pointerDown(grip('a'), { button: 2, pointerId: 6, clientX: 0, clientY: 0 });
  pointAt(screen.getByRole('button', { name: 'Основные' }));
  fireEvent.pointerMove(document, { pointerId: 6, clientX: 30, clientY: 0 });
  expect(screen.queryByTestId('drag-ghost')).not.toBeInTheDocument();
});
