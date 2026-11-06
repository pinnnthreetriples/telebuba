import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { pointAtOffset } from '../model/richText';
import type { CaptionValue } from '../model/types';
import { CaptionEditor } from './CaptionEditor';

function Harness({
  initial = { text: '', entities: [] },
  premium = true,
  onValue,
}: {
  initial?: CaptionValue;
  premium?: boolean | null;
  onValue?: (value: CaptionValue) => void;
}) {
  const [value, setValue] = useState<CaptionValue>(initial);
  return (
    <CaptionEditor
      value={value}
      premium={premium}
      onChange={(next) => {
        setValue(next);
        onValue?.(next);
      }}
    />
  );
}

const editor = () => screen.getByRole('textbox');

function select(start: number, end: number) {
  const root = editor();
  const from = pointAtOffset(root, start);
  const to = pointAtOffset(root, end);
  const range = document.createRange();
  range.setStart(from.node, from.offset);
  range.setEnd(to.node, to.offset);
  const selection = document.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
  act(() => {
    document.dispatchEvent(new Event('selectionchange'));
  });
}

const HELLO: CaptionValue = { text: 'hello world', entities: [] };

function last(spy: ReturnType<typeof vi.fn>): CaptionValue {
  return spy.mock.calls.at(-1)?.[0] as CaptionValue;
}

test('the editor is an accessible multiline textbox with a labelled toolbar', () => {
  render(<Harness />);

  expect(editor()).toHaveAttribute('aria-multiline', 'true');
  expect(editor()).toHaveAttribute('contenteditable', 'true');
  expect(screen.getByRole('toolbar', { name: 'Форматирование' })).toBeInTheDocument();
  expect(screen.getAllByRole('button')).toHaveLength(7);
  expect(screen.getByRole('button', { name: 'Жирный' })).toHaveAttribute('aria-pressed', 'false');
});

test('typing reports the text and leaves the DOM alone', () => {
  const spy = vi.fn();
  render(<Harness onValue={spy} />);

  const node = document.createTextNode('привет');
  editor().appendChild(node);
  fireEvent.input(editor());

  expect(last(spy)).toEqual({ text: 'привет', entities: [] });
  expect(editor().firstChild).toBe(node);
  expect(screen.getByText('6 / 2048')).toBeInTheDocument();
});

test('toolbar buttons need a selection', () => {
  render(<Harness initial={HELLO} />);

  expect(screen.getByRole('button', { name: 'Жирный' })).toBeDisabled();
  select(0, 5);
  expect(screen.getByRole('button', { name: 'Жирный' })).toBeEnabled();
});

test('Bold toggles on the selection and reports its pressed state', async () => {
  const spy = vi.fn();
  render(<Harness initial={HELLO} onValue={spy} />);

  select(0, 5);
  await userEvent.click(screen.getByRole('button', { name: 'Жирный' }));
  expect(last(spy).entities).toEqual([{ type: 'bold', offset: 0, length: 5 }]);
  expect(editor().querySelector('strong')?.textContent).toBe('hello');

  select(1, 3);
  expect(screen.getByRole('button', { name: 'Жирный' })).toHaveAttribute('aria-pressed', 'true');
  await userEvent.click(screen.getByRole('button', { name: 'Жирный' }));
  expect(last(spy).entities).toEqual([
    { type: 'bold', offset: 0, length: 1 },
    { type: 'bold', offset: 3, length: 2 },
  ]);
});

test('every toolbar mark nests with the others', async () => {
  const spy = vi.fn();
  render(<Harness initial={HELLO} onValue={spy} />);

  select(0, 5);
  await userEvent.click(screen.getByRole('button', { name: 'Жирный' }));
  select(0, 5);
  await userEvent.click(screen.getByRole('button', { name: 'Курсив' }));
  select(0, 5);
  await userEvent.click(screen.getByRole('button', { name: 'Подчёркнутый' }));
  select(0, 5);
  await userEvent.click(screen.getByRole('button', { name: 'Зачёркнутый' }));
  select(0, 5);
  await userEvent.click(screen.getByRole('button', { name: 'Спойлер' }));
  select(0, 5);
  await userEvent.click(screen.getByRole('button', { name: 'Моноширинный' }));

  expect(last(spy).entities.map((entity) => entity.type)).toEqual([
    'bold',
    'italic',
    'underline',
    'strike',
    'spoiler',
    'code',
  ]);
  expect(last(spy).text).toBe('hello world');
});

test('shortcuts: Ctrl/Cmd+B, I, U and Ctrl+Shift+X, P, M', () => {
  const spy = vi.fn();
  render(<Harness initial={HELLO} onValue={spy} />);

  const cases: [Parameters<typeof fireEvent.keyDown>[1], string][] = [
    [{ key: 'b', ctrlKey: true }, 'bold'],
    [{ key: 'I', metaKey: true }, 'italic'],
    [{ key: 'u', ctrlKey: true }, 'underline'],
    [{ key: 'X', ctrlKey: true, shiftKey: true }, 'strike'],
    [{ key: 'P', metaKey: true, shiftKey: true }, 'spoiler'],
    [{ key: 'm', ctrlKey: true, shiftKey: true }, 'code'],
  ];
  for (const [keys, type] of cases) {
    select(0, 5);
    fireEvent.keyDown(editor(), keys);
    expect(last(spy).entities.some((entity) => entity.type === type)).toBe(true);
  }
});

test('a shortcut with the wrong modifiers does nothing', () => {
  const spy = vi.fn();
  render(<Harness initial={HELLO} onValue={spy} />);

  select(0, 5);
  fireEvent.keyDown(editor(), { key: 'b' });
  fireEvent.keyDown(editor(), { key: 'b', ctrlKey: true, shiftKey: true });
  fireEvent.keyDown(editor(), { key: 'x', ctrlKey: true });
  fireEvent.keyDown(editor(), { key: 'b', ctrlKey: true, altKey: true });
  fireEvent.keyDown(editor(), { key: 'q', ctrlKey: true });
  expect(spy).not.toHaveBeenCalled();
});

test('a shortcut on a bare caret does nothing', () => {
  const spy = vi.fn();
  render(<Harness initial={HELLO} onValue={spy} />);

  select(3, 3);
  fireEvent.keyDown(editor(), { key: 'b', ctrlKey: true });
  expect(spy).not.toHaveBeenCalled();
});

test('Enter inserts a newline and keeps the marks aligned', () => {
  const spy = vi.fn();
  render(
    <Harness
      initial={{ text: 'ab', entities: [{ type: 'bold', offset: 1, length: 1 }] }}
      onValue={spy}
    />,
  );

  select(1, 1);
  fireEvent.keyDown(editor(), { key: 'Enter' });
  expect(last(spy)).toEqual({ text: 'a\nb', entities: [{ type: 'bold', offset: 2, length: 1 }] });

  select(3, 3);
  fireEvent.keyDown(editor(), { key: 'Enter' });
  expect(last(spy).text).toBe('a\nb\n');
  expect(editor().querySelector('br[data-sentinel]')).not.toBeNull();
});

test('Enter during IME composition is left to the browser', () => {
  const spy = vi.fn();
  render(<Harness initial={HELLO} onValue={spy} />);

  fireEvent.keyDown(editor(), { key: 'Enter', isComposing: true });
  expect(spy).not.toHaveBeenCalled();
});

test('paste inserts plain text only, replacing the selection', () => {
  const spy = vi.fn();
  render(<Harness initial={HELLO} onValue={spy} />);

  select(0, 5);
  fireEvent.paste(editor(), {
    clipboardData: {
      getData: (type: string) => (type === 'text/plain' ? 'a\r\nb' : '<b>rich</b>'),
    },
  });

  expect(last(spy)).toEqual({ text: 'a\nb world', entities: [] });
  expect(editor().querySelector('b')).toBeNull();
});

test('an empty paste and a drop change nothing', () => {
  const spy = vi.fn();
  render(<Harness initial={HELLO} onValue={spy} />);

  fireEvent.paste(editor(), { clipboardData: { getData: () => '' } });
  expect(fireEvent.drop(editor())).toBe(false);
  expect(spy).not.toHaveBeenCalled();
});

test('the link popover validates, normalises and applies the address', async () => {
  const spy = vi.fn();
  render(<Harness initial={HELLO} onValue={spy} />);

  select(0, 5);
  fireEvent.keyDown(editor(), { key: 'k', ctrlKey: true });
  const field = screen.getByLabelText('Адрес ссылки');
  expect(field).toHaveFocus();

  await userEvent.type(field, 'not a url');
  await userEvent.click(screen.getByRole('button', { name: 'Применить' }));
  expect(screen.getByRole('alert')).toHaveTextContent('Нужен адрес http, https или tg');
  expect(field).toHaveAttribute('aria-invalid', 'true');
  expect(spy).not.toHaveBeenCalled();

  await userEvent.clear(field);
  expect(screen.queryByRole('alert')).toBeNull();
  await userEvent.type(field, 'example.com{Enter}');

  expect(last(spy).entities).toEqual([
    { type: 'text_url', offset: 0, length: 5, url: 'https://example.com' },
  ]);
  expect(screen.queryByLabelText('Адрес ссылки')).toBeNull();
  expect(editor().querySelector('a')?.getAttribute('data-url')).toBe('https://example.com');
});

test('an existing link opens prefilled and can be removed', async () => {
  const spy = vi.fn();
  render(
    <Harness
      initial={{
        text: 'hello world',
        entities: [{ type: 'text_url', offset: 0, length: 5, url: 'https://a.io' }],
      }}
      onValue={spy}
    />,
  );

  select(0, 5);
  await userEvent.click(screen.getByRole('button', { name: 'Ссылка' }));
  expect(screen.getByLabelText('Адрес ссылки')).toHaveValue('https://a.io');

  await userEvent.click(screen.getByRole('button', { name: 'Убрать ссылку' }));
  expect(last(spy).entities).toEqual([]);
});

test('Escape and Cancel close the popover without touching the text', async () => {
  const spy = vi.fn();
  render(<Harness initial={HELLO} onValue={spy} />);

  select(0, 5);
  fireEvent.keyDown(editor(), { key: 'k', metaKey: true });
  fireEvent.keyDown(screen.getByLabelText('Адрес ссылки'), { key: 'Escape' });
  expect(screen.queryByLabelText('Адрес ссылки')).toBeNull();

  select(0, 5);
  await userEvent.click(screen.getByRole('button', { name: 'Ссылка' }));
  await userEvent.click(screen.getByRole('button', { name: 'Отмена' }));
  expect(screen.queryByLabelText('Адрес ссылки')).toBeNull();
  expect(spy).not.toHaveBeenCalled();
});

test('an outside change rebuilds the DOM, an echo of our own value does not', () => {
  const { rerender } = render(
    <CaptionEditor value={{ text: 'one', entities: [] }} premium onChange={() => undefined} />,
  );
  const first = editor().firstChild;

  rerender(
    <CaptionEditor value={{ text: 'one', entities: [] }} premium onChange={() => undefined} />,
  );
  expect(editor().firstChild).toBe(first);

  rerender(
    <CaptionEditor
      value={{ text: 'two', entities: [{ type: 'italic', offset: 0, length: 3 }] }}
      premium
      onChange={() => undefined}
    />,
  );
  expect(editor().querySelector('em')?.textContent).toBe('two');
});

test('an outside change keeps the caret of a focused editor', () => {
  const { rerender } = render(
    <CaptionEditor value={{ text: 'abcdef', entities: [] }} premium onChange={() => undefined} />,
  );
  editor().focus();
  select(2, 4);

  rerender(
    <CaptionEditor value={{ text: 'abcdXY', entities: [] }} premium onChange={() => undefined} />,
  );

  const range = document.getSelection()?.getRangeAt(0);
  expect(range?.toString()).toBe('cd');
});

test('the counter warns past the free limit only when Premium is known to be missing', () => {
  const long = { text: 'x'.repeat(201), entities: [] };
  const { rerender } = render(<Harness initial={long} premium={false} />);
  expect(screen.getByText(/Свыше 200 символов — только с Premium/)).toBeInTheDocument();
  expect(screen.getByText(/201 \/ 2048/)).toBeInTheDocument();

  rerender(<Harness initial={long} premium={true} />);
  expect(screen.queryByText(/Свыше 200/)).toBeNull();
});

test('text past 2048 UTF-16 units is cut back on input', () => {
  const spy = vi.fn();
  render(<Harness onValue={spy} />);

  editor().appendChild(document.createTextNode('y'.repeat(3000)));
  fireEvent.input(editor());

  expect(last(spy).text).toHaveLength(2048);
  expect(screen.getByText('2048 / 2048')).toBeInTheDocument();
});

test('the 2048 cut never leaves half an emoji behind', () => {
  const spy = vi.fn();
  render(<Harness onValue={spy} />);

  // 2047 units, then an emoji (two units) straddling the limit.
  editor().appendChild(document.createTextNode(`${'y'.repeat(2047)}😀`));
  fireEvent.input(editor());

  expect(last(spy).text).toBe('y'.repeat(2047));
});

test('a disabled editor cannot be typed in or formatted', () => {
  render(<CaptionEditor value={HELLO} premium onChange={() => undefined} disabled />);

  expect(editor()).toHaveAttribute('contenteditable', 'false');
  expect(editor()).toHaveAttribute('aria-disabled', 'true');
  expect(screen.getByRole('button', { name: 'Жирный' })).toBeDisabled();
});

test('a custom label and placeholder are used, and the placeholder hides once there is text', () => {
  const { rerender } = render(
    <CaptionEditor
      value={{ text: '', entities: [] }}
      premium={null}
      onChange={() => undefined}
      label="Описание"
      placeholder="Пусто"
    />,
  );
  expect(screen.getByRole('textbox', { name: 'Описание' })).toBeInTheDocument();
  expect(screen.getByText('Пусто')).toBeInTheDocument();

  rerender(
    <CaptionEditor
      value={{ text: 'a', entities: [] }}
      premium={null}
      onChange={() => undefined}
      label="Описание"
      placeholder="Пусто"
    />,
  );
  expect(screen.queryByText('Пусто')).toBeNull();
});
