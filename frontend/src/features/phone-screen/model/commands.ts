// The phone screen's wire: the WebSocket address and the commands the backend takes
// (`schemas/device_farm.py`, `ScreenCommand`). The socket carries the video as binary
// frames and these commands as JSON text frames; nothing here speaks scrcpy itself,
// the backend writes its bytes.

export type ScreenMode = 'view' | 'control';

export type FramePoint = { x: number; y: number; width: number; height: number };

export type ScreenCommand =
  | ({ type: 'touch'; action: 'down' | 'move' | 'up' } & FramePoint)
  | ({ type: 'scroll'; dx: number; dy: number } & FramePoint)
  | { type: 'key'; action: 'down' | 'up'; keycode: number; meta?: number }
  | { type: 'text'; text: string }
  | { type: 'clipboard_set'; text: string; paste: boolean }
  | { type: 'clipboard_get' };

// What the backend says on the text channel.
export type ScreenMessage =
  { type: 'ready'; controllable: boolean } | { type: 'clipboard'; text: string };

// Android KEYCODE_* for the phone's own buttons.
export const PHONE_BUTTONS = { back: 4, home: 3, recents: 187 } as const;
export type PhoneButton = keyof typeof PHONE_BUTTONS;

// The keyboard keys that are no character: Escape is the phone's Back.
const KEYCODES: Record<string, number> = {
  Backspace: 67,
  Delete: 112,
  Enter: 66,
  Tab: 61,
  Escape: 4,
  ArrowUp: 19,
  ArrowDown: 20,
  ArrowLeft: 21,
  ArrowRight: 22,
  Home: 122,
  End: 123,
  PageUp: 92,
  PageDown: 93,
};

// `input text` on the phone takes at most this many characters per command.
const TEXT_MAX = 300;
// One wheel notch (100 px in most browsers) is one scrcpy scroll step; ±16 is its range.
const WHEEL_STEP = 100;
const SCROLL_MAX = 16;

export function screenSocketUrl(phoneId: string, mode: ScreenMode, page: Location): string {
  const scheme = page.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${scheme}//${page.host}/api/v1/devices/${encodeURIComponent(phoneId)}/screen?mode=${mode}`;
}

type Box = Pick<DOMRect, 'left' | 'top' | 'width' | 'height'>;

// Where the frame is actually painted inside an element that letterboxes it
// (`object-fit: contain`): the element keeps a phone's shape, the stream may not.
export function containedBox(box: Box, frame: { width: number; height: number }): Box {
  if (box.width <= 0 || box.height <= 0 || frame.width <= 0 || frame.height <= 0) return box;
  const scale = Math.min(box.width / frame.width, box.height / frame.height);
  const width = frame.width * scale;
  const height = frame.height * scale;
  return {
    left: box.left + (box.width - width) / 2,
    top: box.top + (box.height - height) / 2,
    width,
    height,
  };
}

// A pointer position on the shown canvas as a point on the video frame, or null
// outside it. The frame size travels along: the phone drops an event whose size is
// not the one it encodes at, so a click from before a rotation cannot land elsewhere.
export function framePoint(
  clientX: number,
  clientY: number,
  box: Pick<DOMRect, 'left' | 'top' | 'width' | 'height'>,
  frame: { width: number; height: number },
): FramePoint | null {
  if (box.width <= 0 || box.height <= 0 || frame.width <= 0 || frame.height <= 0) return null;
  const rx = (clientX - box.left) / box.width;
  const ry = (clientY - box.top) / box.height;
  if (rx < 0 || ry < 0 || rx > 1 || ry > 1) return null;
  return {
    x: Math.min(frame.width - 1, Math.round(rx * frame.width)),
    y: Math.min(frame.height - 1, Math.round(ry * frame.height)),
    width: frame.width,
    height: frame.height,
  };
}

export function buttonPress(button: PhoneButton): ScreenCommand[] {
  const keycode = PHONE_BUTTONS[button];
  return [
    { type: 'key', action: 'down', keycode },
    { type: 'key', action: 'up', keycode },
  ];
}

// A key pressed over the screen as the phone's commands; null for one it ignores
// (a shortcut the browser keeps, a lone modifier).
export function keyCommands(event: {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
}): ScreenCommand[] | null {
  if (event.ctrlKey || event.metaKey || event.altKey) return null;
  const keycode = KEYCODES[event.key];
  if (keycode !== undefined) {
    return [
      { type: 'key', action: 'down', keycode },
      { type: 'key', action: 'up', keycode },
    ];
  }
  // A printable key has a one-character name ("a", "Я", " "); named keys are longer.
  if ([...event.key].length === 1) return textCommands(event.key);
  return null;
}

// scrcpy types text through the phone's virtual keyboard map, which has keys for
// ASCII only: a Cyrillic letter or an emoji would be dropped without a word. Such
// text goes the way scrcpy's own Ctrl+V does — onto the phone's clipboard, pasted.
const ASCII_ONLY = /^[ -~]*$/;
const CLIPBOARD_MAX = 65536;

// Typed or pasted text as the phone's commands: ASCII in `input text`-sized pieces,
// anything else pasted through the clipboard.
export function textCommands(text: string): ScreenCommand[] {
  if (!text) return [];
  if (!ASCII_ONLY.test(text)) {
    return [{ type: 'clipboard_set', text: text.slice(0, CLIPBOARD_MAX), paste: true }];
  }
  const chars = [...text];
  const commands: ScreenCommand[] = [];
  for (let start = 0; start < chars.length; start += TEXT_MAX) {
    commands.push({ type: 'text', text: chars.slice(start, start + TEXT_MAX).join('') });
  }
  return commands;
}

export function wheelCommand(point: FramePoint, deltaX: number, deltaY: number): ScreenCommand {
  // `+ 0` turns a no-move's -0 into 0.
  const step = (delta: number) =>
    Math.max(-SCROLL_MAX, Math.min(SCROLL_MAX, -delta / WHEEL_STEP)) + 0;
  return { type: 'scroll', ...point, dx: step(deltaX), dy: step(deltaY) };
}

export function parseScreenMessage(text: string): ScreenMessage | null {
  try {
    const message: unknown = JSON.parse(text);
    if (typeof message !== 'object' || message === null || !('type' in message)) return null;
    if (message.type === 'ready' && 'controllable' in message) {
      return { type: 'ready', controllable: message.controllable === true };
    }
    if (message.type === 'clipboard' && 'text' in message && typeof message.text === 'string') {
      return { type: 'clipboard', text: message.text };
    }
    return null;
  } catch {
    return null;
  }
}

// The close reasons the backend sends (`ScreenCloseCode` and the refusal codes);
// anything else, or a dropped connection, reads as a lost screen.
const KNOWN_REASONS = new Set([
  'screen_ended',
  'screen_superseded',
  'screen_bad_command',
  'emulator_busy',
  'emulator_not_running',
  'emulator_not_found',
  'emulator_scrcpy_missing',
  'emulator_download_failed',
  'emulator_checksum_mismatch',
  'emulator_screen_timeout',
  'mobile_emulator_disabled',
]);

export function closeReason(code: number, reason: string): string {
  if (KNOWN_REASONS.has(reason)) return reason;
  if (code === 1008) return 'screen_forbidden';
  return 'screen_lost';
}
