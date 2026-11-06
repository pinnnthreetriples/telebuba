import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { LinkSticker, StoryMedia } from '../model/types';
import { StoryCanvas } from './StoryCanvas';

const CANVAS = { left: 0, top: 0, width: 200, height: 400 };
const PLATE = { left: 0, top: 0, width: 80, height: 24 };

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
    this: HTMLElement,
  ) {
    const box = this.hasAttribute('data-sticker-id') ? PLATE : CANVAS;
    return { ...box, right: box.width, bottom: box.height, x: 0, y: 0, toJSON: () => box };
  });
});
afterEach(() => {
  vi.restoreAllMocks();
});

const sticker = (patch: Partial<LinkSticker> = {}): LinkSticker => ({
  id: 's1',
  url: 'https://example.com',
  label: 'Подробнее',
  style: 'white',
  x: 50,
  y: 50,
  scale: 1,
  rotation: 0,
  ...patch,
});

function Harness({
  initial = [sticker()],
  media = null,
  disabled = false,
  onSelectSpy,
}: {
  initial?: LinkSticker[];
  media?: StoryMedia | null;
  disabled?: boolean;
  onSelectSpy?: (id: string | null) => void;
}) {
  const [stickers, setStickers] = useState(initial);
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <StoryCanvas
      media={media}
      stickers={stickers}
      onChange={setStickers}
      selectedId={selected}
      onSelect={(id) => {
        setSelected(id);
        onSelectSpy?.(id);
      }}
      disabled={disabled}
    />
  );
}

const pill = () => screen.getByRole('button', { name: /Ссылка:/ });
const pointer = (x: number, y: number) => ({ clientX: x, clientY: y, pointerId: 1 });

test('the canvas is a 9:16 box that shows an image, a video frame or a collage', () => {
  const { container, rerender } = render(
    <StoryCanvas
      media={{ kind: 'image', url: 'a.jpg' }}
      stickers={[]}
      onChange={() => undefined}
      selectedId={null}
      onSelect={() => undefined}
    />,
  );
  const canvas = screen.getByRole('group', { name: 'Предпросмотр истории' });
  expect(canvas.style.aspectRatio).toBe('9 / 16');
  expect(container.querySelector('img')).toHaveAttribute('src', 'a.jpg');

  const props = {
    stickers: [],
    onChange: () => undefined,
    selectedId: null,
    onSelect: () => undefined,
  };
  rerender(<StoryCanvas media={{ kind: 'video', url: 'v.mp4' }} {...props} />);
  const video = container.querySelector('video');
  expect(video).toHaveAttribute('src', 'v.mp4#t=0.1');
  expect(video?.muted).toBe(true);

  rerender(<StoryCanvas media={{ kind: 'collage', urls: ['1', '2', '3', '4', '5'] }} {...props} />);
  expect(container.querySelectorAll('img')).toHaveLength(4);

  rerender(
    <StoryCanvas media={{ kind: 'collage', urls: ['1', '2'], layout: 'rows' }} {...props} />,
  );
  expect(container.querySelectorAll('img')).toHaveLength(1);

  rerender(<StoryCanvas media={{ kind: 'collage', urls: ['1'] }} {...props} />);
  expect(container.querySelectorAll('img')).toHaveLength(1);

  rerender(<StoryCanvas media={null} {...props} />);
  expect(screen.getByText('Нет медиа')).toBeInTheDocument();
});

test('a sticker is a plate at its centre, rotated and scaled', () => {
  render(<Harness initial={[sticker({ x: 30, y: 70, rotation: 20, scale: 1.5 })]} />);

  expect(pill().style.left).toBe('30%');
  expect(pill().style.top).toBe('70%');
  expect(pill().style.transform).toContain('rotate(20deg)');
  expect(pill().style.transform).toContain('scale(1.5)');
  expect(pill()).toHaveTextContent('Подробнее');
});

test('every style paints from its own tokens', () => {
  const classes: Record<string, string> = {};
  for (const style of ['white', 'dark', 'glass', 'accent'] as const) {
    const { unmount } = render(<Harness initial={[sticker({ style })]} />);
    classes[style] = pill().className;
    unmount();
  }
  expect(classes.white).toContain('bg-surface-card');
  expect(classes.dark).toContain('bg-term');
  expect(classes.glass).toContain('bg-surface-card/30');
  expect(classes.accent).toContain('bg-action-primary');
});

test('dragging moves the sticker by the pointer delta in percent and selects it', () => {
  const picked = vi.fn();
  render(<Harness onSelectSpy={picked} />);

  fireEvent.pointerDown(pill(), pointer(100, 200));
  expect(picked).toHaveBeenCalledWith('s1');
  expect(pill()).toHaveAttribute('aria-pressed', 'true');

  fireEvent.pointerMove(pill(), pointer(150, 160));
  expect(pill().style.left).toBe('75%');
  expect(pill().style.top).toBe('40%');

  fireEvent.pointerUp(pill(), pointer(150, 160));
  fireEvent.pointerMove(pill(), pointer(0, 0));
  expect(pill().style.left).toBe('75%');
});

test('dragging past the edge keeps the whole plate on the canvas', () => {
  render(<Harness />);

  fireEvent.pointerDown(pill(), pointer(100, 200));
  fireEvent.pointerMove(pill(), pointer(900, 900));
  expect(pill().style.left).toBe('80%');
  expect(pill().style.top).toBe('97%');

  fireEvent.pointerMove(pill(), pointer(-900, -900));
  expect(pill().style.left).toBe('20%');
  expect(pill().style.top).toBe('3%');
});

test('the corner handle scales from the centre and stops at 2.5', () => {
  render(<Harness />);
  fireEvent.pointerDown(pill(), pointer(100, 200));
  fireEvent.pointerUp(pill(), pointer(100, 200));

  const handle = pill().querySelector('[data-handle="scale"]') as HTMLElement;
  fireEvent.pointerDown(handle, pointer(160, 212));
  fireEvent.pointerMove(handle, pointer(220, 224));
  expect(pill().style.transform).toContain('scale(2)');

  fireEvent.pointerMove(handle, pointer(1000, 224));
  expect(pill().style.transform).toContain('scale(2.5)');

  fireEvent.pointerMove(handle, pointer(101, 200));
  expect(pill().style.transform).toContain('scale(0.5)');
});

test('the rotate handle points the sticker at the pointer, Shift snapping to 15 degrees', () => {
  render(<Harness />);
  fireEvent.pointerDown(pill(), pointer(100, 200));
  fireEvent.pointerUp(pill(), pointer(100, 200));

  const handle = pill().querySelector('[data-handle="rotate"]') as HTMLElement;
  fireEvent.pointerDown(handle, pointer(100, 170));
  fireEvent.pointerMove(handle, pointer(300, 200));
  expect(pill().style.transform).toContain('rotate(90deg)');

  fireEvent.pointerMove(handle, pointer(100, 300));
  expect(pill().style.transform).toContain('rotate(180deg)');

  fireEvent.pointerMove(handle, { ...pointer(140, 100), shiftKey: true });
  expect(pill().style.transform).toContain('rotate(15deg)');
});

test('handles appear only on the selected sticker', () => {
  render(<Harness />);
  expect(pill().querySelector('[data-handle]')).toBeNull();

  fireEvent.pointerDown(pill(), pointer(100, 200));
  expect(pill().querySelectorAll('[data-handle]')).toHaveLength(2);

  fireEvent.pointerDown(screen.getByRole('group', { name: 'Предпросмотр истории' }), pointer(5, 5));
  expect(pill().querySelector('[data-handle]')).toBeNull();
});

test('arrow keys nudge by 1% and Shift by 5%', () => {
  render(<Harness />);

  pill().focus();
  fireEvent.keyDown(pill(), { key: 'ArrowRight' });
  expect(pill().style.left).toBe('51%');
  fireEvent.keyDown(pill(), { key: 'ArrowUp', shiftKey: true });
  expect(pill().style.top).toBe('45%');
  fireEvent.keyDown(pill(), { key: 'ArrowLeft' });
  fireEvent.keyDown(pill(), { key: 'ArrowDown' });
  expect(pill().style.left).toBe('50%');
  expect(pill().style.top).toBe('46%');
});

test('+ and - resize, [ and ] rotate', () => {
  render(<Harness />);

  fireEvent.keyDown(pill(), { key: '+' });
  expect(pill().style.transform).toContain('scale(1.1)');
  fireEvent.keyDown(pill(), { key: '-' });
  fireEvent.keyDown(pill(), { key: '-' });
  expect(pill().style.transform).toContain('scale(0.9)');
  fireEvent.keyDown(pill(), { key: ']' });
  expect(pill().style.transform).toContain('rotate(5deg)');
  fireEvent.keyDown(pill(), { key: '[' });
  fireEvent.keyDown(pill(), { key: '[' });
  expect(pill().style.transform).toContain('rotate(-5deg)');
  fireEvent.keyDown(pill(), { key: 'q' });
});

test('Delete and Backspace remove the sticker and clear the selection', () => {
  const picked = vi.fn();
  render(
    <Harness initial={[sticker(), sticker({ id: 's2', label: 'Второй' })]} onSelectSpy={picked} />,
  );

  fireEvent.keyDown(screen.getAllByRole('button', { name: /Ссылка:/ })[0] as HTMLElement, {
    key: 'Delete',
  });
  expect(screen.getAllByRole('button', { name: /Ссылка:/ })).toHaveLength(1);
  expect(picked).toHaveBeenLastCalledWith(null);

  fireEvent.keyDown(pill(), { key: 'Backspace' });
  expect(screen.queryByRole('button', { name: /Ссылка:/ })).toBeNull();
});

test('double click edits the label in place; Enter commits', async () => {
  render(<Harness />);

  fireEvent.doubleClick(pill());
  const field = screen.getByLabelText('Название ссылки');
  expect(field).toHaveValue('Подробнее');

  await userEvent.clear(field);
  await userEvent.type(field, 'Читать{Enter}');
  expect(screen.queryByLabelText('Название ссылки')).toBeNull();
  expect(pill()).toHaveTextContent('Читать');
});

test('an emoji label is refused and Escape leaves the old one', async () => {
  render(<Harness />);

  fireEvent.keyDown(pill(), { key: 'Enter' });
  const field = screen.getByLabelText('Название ссылки');
  await userEvent.type(field, ' 🔥{Enter}');
  expect(screen.getByLabelText('Название ссылки')).toHaveAttribute('aria-invalid', 'true');

  await userEvent.keyboard('{Escape}');
  expect(screen.queryByLabelText('Название ссылки')).toBeNull();
  expect(pill()).toHaveTextContent('Подробнее');
});

test('leaving the field with an invalid label drops the edit', async () => {
  render(<Harness />);

  fireEvent.keyDown(pill(), { key: 'F2' });
  const field = screen.getByLabelText('Название ссылки');
  await userEvent.clear(field);
  fireEvent.blur(field);

  expect(screen.queryByLabelText('Название ссылки')).toBeNull();
  expect(pill()).toHaveTextContent('Подробнее');
});

test('keys typed in the label field do not move or delete the sticker', async () => {
  render(<Harness />);

  fireEvent.keyDown(pill(), { key: 'Enter' });
  const field = screen.getByLabelText('Название ссылки');
  await userEvent.type(field, '{Backspace}{ArrowRight}');
  expect(screen.getAllByRole('button', { name: /Ссылка:/ })).toHaveLength(1);
  expect(pill().style.left).toBe('50%');
});

test('a disabled canvas ignores pointer and keyboard', () => {
  const picked = vi.fn();
  render(<Harness disabled onSelectSpy={picked} />);

  fireEvent.pointerDown(pill(), pointer(100, 200));
  fireEvent.keyDown(pill(), { key: 'Delete' });
  fireEvent.keyDown(pill(), { key: 'ArrowRight' });
  fireEvent.doubleClick(pill());

  expect(picked).not.toHaveBeenCalled();
  expect(pill().style.left).toBe('50%');
  expect(screen.queryByLabelText('Название ссылки')).toBeNull();
});

test('an empty label shows the placeholder word on the plate', () => {
  render(<Harness initial={[sticker({ label: '' })]} />);
  expect(pill()).toHaveTextContent('Подробнее');
});
