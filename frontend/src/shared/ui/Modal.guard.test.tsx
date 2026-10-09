import '@/shared/i18n';

import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, expect, test, vi } from 'vitest';

import { Modal } from './Modal';
import { useModalDirty } from './useModalDirty';

// Modal's guard against losing edits: a `dirty` dialog never closes silently on a
// dismissal — backdrop, Escape, or the body's own Cancel/× — and asks instead.

afterEach(() => {
  document.body.style.overflow = '';
});

const QUESTION = 'Закрыть без сохранения?';

function Editor({ dirty, onClose }: { dirty: boolean; onClose: () => void }) {
  return (
    <Modal onClose={onClose} dirty={dirty} label="Редактор">
      {(close) => (
        <>
          <input aria-label="Поле" />
          <button type="button" aria-label="Закрыть" onClick={close}>
            ×
          </button>
          <button type="button" onClick={close}>
            Отмена
          </button>
        </>
      )}
    </Modal>
  );
}

const editorDialog = () => screen.getByRole('dialog', { name: 'Редактор' });
const overlayOf = (dialog: HTMLElement) => dialog.parentElement as HTMLElement;

test('a clean dialog closes at once on backdrop, Escape and Cancel — no question', async () => {
  const onClose = vi.fn();
  render(<Editor dirty={false} onClose={onClose} />);

  await userEvent.click(overlayOf(editorDialog()));
  await userEvent.keyboard('{Escape}');
  await userEvent.click(screen.getByText('Отмена'));

  expect(onClose).toHaveBeenCalledTimes(3);
  expect(screen.queryByText(QUESTION)).not.toBeInTheDocument();
});

test('a dirty dialog asks on a backdrop click, and «Остаться» keeps it open', async () => {
  const onClose = vi.fn();
  render(<Editor dirty onClose={onClose} />);

  await userEvent.click(overlayOf(editorDialog()));
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByRole('dialog', { name: QUESTION })).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'Остаться' }));
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.queryByText(QUESTION)).not.toBeInTheDocument();
  expect(editorDialog()).toBeInTheDocument();
  // The backdrop click had dropped focus onto <body>; staying hands it back.
  expect(editorDialog().contains(document.activeElement)).toBe(true);
});

test('«Закрыть без сохранения» closes the dirty dialog', async () => {
  const onClose = vi.fn();
  render(<Editor dirty onClose={onClose} />);

  await userEvent.click(screen.getByText('Отмена'));
  expect(onClose).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: 'Закрыть без сохранения' }));
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('the × close button asks as well', async () => {
  const onClose = vi.fn();
  render(<Editor dirty onClose={onClose} />);

  await userEvent.click(screen.getByLabelText('Закрыть'));
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByText(QUESTION)).toBeInTheDocument();
});

test('Escape asks; Escape on the question closes only the question', async () => {
  const onClose = vi.fn();
  render(<Editor dirty onClose={onClose} />);

  await userEvent.keyboard('{Escape}');
  expect(screen.getByText(QUESTION)).toBeInTheDocument();

  // The question is on top of the modal stack, so only it handles this one.
  await userEvent.keyboard('{Escape}');
  expect(screen.queryByText(QUESTION)).not.toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
  expect(editorDialog()).toBeInTheDocument();
});

test('a backdrop click on the question means «stay», not «discard»', async () => {
  const onClose = vi.fn();
  render(<Editor dirty onClose={onClose} />);

  await userEvent.keyboard('{Escape}');
  await userEvent.click(overlayOf(screen.getByRole('dialog', { name: QUESTION })));
  expect(screen.queryByText(QUESTION)).not.toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
});

// The question is a second dialog: it must not take the page scroll lock with it, and
// discarding (which unmounts both at once) must hand the page its scroll back.
test('the scroll lock survives the question and is released when the dialog goes', async () => {
  function Host() {
    const [open, setOpen] = useState(true);
    return open ? (
      <Editor
        dirty
        onClose={() => {
          setOpen(false);
        }}
      />
    ) : null;
  }
  render(<Host />);
  expect(document.body.style.overflow).toBe('hidden');

  await userEvent.keyboard('{Escape}');
  await userEvent.click(screen.getByRole('button', { name: 'Остаться' }));
  expect(document.body.style.overflow).toBe('hidden');

  await userEvent.keyboard('{Escape}');
  await userEvent.click(screen.getByRole('button', { name: 'Закрыть без сохранения' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(document.body.style.overflow).toBe('');
});

test('a locked dialog neither closes nor asks, even when dirty', async () => {
  const onClose = vi.fn();
  render(
    <Modal onClose={onClose} dirty locked label="Редактор">
      <div>загрузка</div>
    </Modal>,
  );

  await userEvent.click(overlayOf(editorDialog()));
  await userEvent.keyboard('{Escape}');
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.queryByText(QUESTION)).not.toBeInTheDocument();
});

// A step deep in the body owns its own input and reports it without lifting state.
test('useModalDirty makes the enclosing dialog ask while the part holds input', async () => {
  function Step() {
    const [text, setText] = useState('');
    useModalDirty(text !== '');
    return (
      <input
        aria-label="Код"
        value={text}
        onChange={(event) => {
          setText(event.target.value);
        }}
      />
    );
  }
  const onClose = vi.fn();
  render(
    <Modal onClose={onClose} label="Редактор">
      <Step />
    </Modal>,
  );

  await userEvent.type(screen.getByLabelText('Код'), '12');
  await userEvent.keyboard('{Escape}');
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByText(QUESTION)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Остаться' }));

  // Cleared again: nothing to lose, so the next Escape closes straight away.
  await userEvent.clear(screen.getByLabelText('Код'));
  await userEvent.keyboard('{Escape}');
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('useModalDirty outside any dialog is a no-op', () => {
  function Loose() {
    useModalDirty(true);
    return <span>без диалога</span>;
  }
  render(<Loose />);
  expect(screen.getByText('без диалога')).toBeInTheDocument();
});

test('a question open when the dialog locks is withdrawn, and never discards mid-write', async () => {
  const onClose = vi.fn();
  function Host() {
    const [locked, setLocked] = useState(false);
    return (
      <>
        <button
          type="button"
          onClick={() => {
            setLocked((value) => !value);
          }}
        >
          замок
        </button>
        <Modal onClose={onClose} dirty locked={locked} label="Редактор">
          <div>тело</div>
        </Modal>
      </>
    );
  }
  render(<Host />);

  await userEvent.keyboard('{Escape}');
  expect(screen.getByText(QUESTION)).toBeInTheDocument();
  fireEvent.click(screen.getByText('замок'));
  expect(screen.queryByText(QUESTION)).not.toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
  expect(editorDialog()).toBeInTheDocument();
});
