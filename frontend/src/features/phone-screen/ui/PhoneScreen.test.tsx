import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { PhoneScreen } from './PhoneScreen';

// The decoder is WebCodecs + WebGL, neither of which the test DOM has: it is replaced
// by one that reports the phone's frame size at once. The socket is a fake that
// records what the screen sends and lets the test speak for the backend.
const decoder = vi.hoisted(() => ({
  supported: true,
  dispose: vi.fn(),
  decode: vi.fn(),
}));

vi.mock('../model/decoder', () => ({
  canDecodeScreen: () => decoder.supported,
  decodeScreen: decoder.decode,
}));

class FakeSocket {
  static readonly OPEN = 1;
  static instances: FakeSocket[] = [];
  readonly url: string;
  readyState = FakeSocket.OPEN;
  binaryType = 'blob';
  sent: unknown[] = [];
  closedWith: number | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: ((event: { code: number; reason: string }) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeSocket.instances.push(this);
  }

  send(text: string) {
    this.sent.push(JSON.parse(text));
  }

  close(code: number) {
    this.closedWith = code;
  }

  // The backend's side.
  say(message: unknown) {
    act(() => this.onmessage?.({ data: JSON.stringify(message) }));
  }

  hangUp(code: number, reason: string) {
    act(() => this.onclose?.({ code, reason }));
  }
}

function socket(): FakeSocket {
  const last = FakeSocket.instances.at(-1);
  if (!last) throw new Error('no socket opened');
  return last;
}

beforeEach(() => {
  FakeSocket.instances = [];
  decoder.supported = true;
  decoder.dispose.mockReset();
  decoder.decode.mockReset();
  decoder.decode.mockImplementation(
    (
      _video: unknown,
      _canvas: unknown,
      onSize: (size: { width: number; height: number }) => void,
    ) => {
      onSize({ width: 720, height: 1280 });
      return Promise.resolve({ dispose: decoder.dispose });
    },
  );
  globalThis.WebSocket = FakeSocket as unknown as typeof WebSocket;
  HTMLCanvasElement.prototype.setPointerCapture = vi.fn();
  HTMLCanvasElement.prototype.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 360, height: 640 }) as DOMRect;
});

// Restored by hand: `vi.unstubAllGlobals` would also take away the setup's fetch stub.
const realWebSocket = globalThis.WebSocket;
const realClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
afterEach(() => {
  globalThis.WebSocket = realWebSocket;
  if (realClipboard) Object.defineProperty(navigator, 'clipboard', realClipboard);
});

async function openControl() {
  render(<PhoneScreen phoneId="telebuba-acc" mode="control" />);
  await act(() => Promise.resolve());
  socket().say({ type: 'ready', controllable: true });
}

test('opens the control socket and presses the phone buttons', async () => {
  await openControl();

  expect(socket().url).toBe(
    `ws://${window.location.host}/api/v1/devices/telebuba-acc/screen?mode=control`,
  );
  await userEvent.click(screen.getByRole('button', { name: 'Домой' }));

  expect(socket().sent).toEqual([
    { type: 'key', action: 'down', keycode: 3 },
    { type: 'key', action: 'up', keycode: 3 },
  ]);
});

test('a drag on the screen is a finger on the phone frame', async () => {
  await openControl();
  const canvas = screen.getByLabelText(/управление мышью/);

  fireEvent.pointerDown(canvas, { button: 0, clientX: 180, clientY: 320, pointerId: 1 });
  fireEvent.pointerMove(canvas, { clientX: 180, clientY: 160, pointerId: 1 });
  fireEvent.pointerUp(canvas, { clientX: 180, clientY: 900, pointerId: 1 });

  const frame = { width: 720, height: 1280 };
  expect(socket().sent).toEqual([
    { type: 'touch', action: 'down', x: 360, y: 640, ...frame },
    { type: 'touch', action: 'move', x: 360, y: 320, ...frame },
    // Lifted below the canvas: lifted at its bottom edge.
    { type: 'touch', action: 'up', x: 360, y: 1279, ...frame },
  ]);
});

test('typing over the screen types on the phone', async () => {
  await openControl();
  const canvas = screen.getByLabelText(/управление мышью/);

  fireEvent.keyDown(canvas, { key: 'ж' });
  fireEvent.keyDown(canvas, { key: 'Enter' });
  fireEvent.keyDown(canvas, { key: 'c', ctrlKey: true });

  expect(socket().sent).toEqual([
    { type: 'clipboard_set', text: 'ж', paste: true },
    { type: 'key', action: 'down', keycode: 66 },
    { type: 'key', action: 'up', keycode: 66 },
  ]);
});

test('the phone clipboard is copied to the browser', async () => {
  const writeText = vi.fn(() => Promise.resolve());
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  await openControl();

  await userEvent.click(screen.getByRole('button', { name: 'Скопировать с телефона' }));
  socket().say({ type: 'clipboard', text: 'код 12345' });

  expect(socket().sent).toEqual([{ type: 'clipboard_get' }]);
  expect(writeText).toHaveBeenCalledWith('код 12345');
});

test('view mode only watches: no buttons, no input', async () => {
  render(<PhoneScreen phoneId="telebuba-acc" mode="view" />);
  await act(() => Promise.resolve());
  socket().say({ type: 'ready', controllable: false });
  const canvas = screen.getByLabelText('Экран телефона');

  fireEvent.pointerDown(canvas, { button: 0, clientX: 10, clientY: 10, pointerId: 1 });
  fireEvent.keyDown(canvas, { key: 'a' });

  expect(socket().url).toMatch(/mode=view$/);
  expect(screen.queryByRole('button', { name: 'Назад' })).toBeNull();
  expect(socket().sent).toEqual([]);
});

test('a refused screen says why', async () => {
  await openControl();

  socket().hangUp(4000, 'emulator_busy');

  expect(screen.getByText(/занят прогревом или запуском/)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Домой' })).toHaveProperty('disabled', true);
});

test('closing the screen closes the socket and the decoder', async () => {
  const { unmount } = render(<PhoneScreen phoneId="telebuba-acc" mode="view" />);
  await act(() => Promise.resolve());

  unmount();

  expect(socket().closedWith).toBe(1000);
  expect(decoder.dispose).toHaveBeenCalled();
});

test('a browser without WebCodecs opens no socket', async () => {
  decoder.supported = false;

  render(<PhoneScreen phoneId="telebuba-acc" mode="control" />);

  expect(await screen.findByText(/WebCodecs/)).toBeTruthy();
  expect(FakeSocket.instances).toEqual([]);
});
