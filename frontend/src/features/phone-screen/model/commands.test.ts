import { describe, expect, it } from 'vitest';

import {
  buttonPress,
  closeReason,
  framePoint,
  keyCommands,
  parseScreenMessage,
  screenSocketUrl,
  textCommands,
  wheelCommand,
} from './commands';

const plain = { ctrlKey: false, metaKey: false, altKey: false };

describe('screenSocketUrl', () => {
  it('follows the page scheme and escapes the phone id', () => {
    const http = { protocol: 'http:', host: 'farm:8080' } as Location;
    const https = { protocol: 'https:', host: 'farm' } as Location;

    expect(screenSocketUrl('telebuba-a b', 'view', http)).toBe(
      'ws://farm:8080/api/v1/devices/telebuba-a%20b/screen?mode=view',
    );
    expect(screenSocketUrl('telebuba-1', 'control', https)).toBe(
      'wss://farm/api/v1/devices/telebuba-1/screen?mode=control',
    );
  });
});

describe('framePoint', () => {
  const box = { left: 100, top: 50, width: 360, height: 640 };
  const frame = { width: 720, height: 1280 };

  it('scales the shown canvas to the video frame', () => {
    expect(framePoint(280, 370, box, frame)).toEqual({ x: 360, y: 640, width: 720, height: 1280 });
  });

  it('keeps the far edge inside the frame', () => {
    expect(framePoint(460, 690, box, frame)).toEqual({ x: 719, y: 1279, width: 720, height: 1280 });
  });

  it('is nothing outside the canvas or before the first frame', () => {
    expect(framePoint(99, 370, box, frame)).toBeNull();
    expect(framePoint(280, 691, box, frame)).toBeNull();
    expect(framePoint(280, 370, box, { width: 0, height: 0 })).toBeNull();
  });
});

describe('keyCommands', () => {
  it('types an ASCII character as text, anything else through the clipboard', () => {
    expect(keyCommands({ key: 'q', ...plain })).toEqual([{ type: 'text', text: 'q' }]);
    expect(keyCommands({ key: ' ', ...plain })).toEqual([{ type: 'text', text: ' ' }]);
    expect(keyCommands({ key: 'Я', ...plain })).toEqual([
      { type: 'clipboard_set', text: 'Я', paste: true },
    ]);
  });

  it('presses a named key as its Android keycode, Escape as Back', () => {
    expect(keyCommands({ key: 'Backspace', ...plain })).toEqual([
      { type: 'key', action: 'down', keycode: 67 },
      { type: 'key', action: 'up', keycode: 67 },
    ]);
    expect(keyCommands({ key: 'Escape', ...plain })?.[0]).toMatchObject({ keycode: 4 });
  });

  it('leaves shortcuts and lone modifiers to the browser', () => {
    expect(keyCommands({ key: 'v', ...plain, ctrlKey: true })).toBeNull();
    expect(keyCommands({ key: 'c', ...plain, metaKey: true })).toBeNull();
    expect(keyCommands({ key: 'Shift', ...plain })).toBeNull();
  });
});

describe('textCommands', () => {
  it('cuts pasted ASCII to what one input command takes', () => {
    const commands = textCommands('e'.repeat(301));

    expect(commands).toHaveLength(2);
    expect(commands[1]).toEqual({ type: 'text', text: 'e' });
  });

  it('pastes text the phone keyboard cannot type through the clipboard', () => {
    expect(textCommands('Привет 👋')).toEqual([
      { type: 'clipboard_set', text: 'Привет 👋', paste: true },
    ]);
    expect(textCommands('')).toEqual([]);
  });
});

describe('buttonPress', () => {
  it('is a press and a release of the phone button', () => {
    expect(buttonPress('home')).toEqual([
      { type: 'key', action: 'down', keycode: 3 },
      { type: 'key', action: 'up', keycode: 3 },
    ]);
    expect(buttonPress('recents')[0]).toMatchObject({ keycode: 187 });
  });
});

describe('wheelCommand', () => {
  it('turns a wheel notch down into one step down, within the range', () => {
    const point = { x: 1, y: 2, width: 3, height: 4 };

    expect(wheelCommand(point, 0, 100)).toEqual({ type: 'scroll', ...point, dx: 0, dy: -1 });
    expect(wheelCommand(point, -5000, 0)).toMatchObject({ dx: 16 });
  });
});

describe('parseScreenMessage', () => {
  it('reads the ready and clipboard messages', () => {
    expect(parseScreenMessage('{"type":"ready","controllable":true}')).toEqual({
      type: 'ready',
      controllable: true,
    });
    expect(parseScreenMessage('{"type":"clipboard","text":"x"}')).toEqual({
      type: 'clipboard',
      text: 'x',
    });
  });

  it('ignores anything else', () => {
    expect(parseScreenMessage('nope')).toBeNull();
    expect(parseScreenMessage('null')).toBeNull();
    expect(parseScreenMessage('{"type":"clipboard","text":1}')).toBeNull();
    expect(parseScreenMessage('{"type":"other"}')).toBeNull();
  });
});

describe('closeReason', () => {
  it('keeps the reasons the backend names', () => {
    expect(closeReason(4000, 'emulator_busy')).toBe('emulator_busy');
    expect(closeReason(1000, 'screen_superseded')).toBe('screen_superseded');
  });

  it('reads a refused handshake and a dropped line', () => {
    expect(closeReason(1008, '')).toBe('screen_forbidden');
    expect(closeReason(1006, '')).toBe('screen_lost');
    expect(closeReason(4000, 'made_up')).toBe('screen_lost');
  });
});
