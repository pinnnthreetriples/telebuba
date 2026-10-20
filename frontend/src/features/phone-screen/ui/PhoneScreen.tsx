import { useCallback, useRef, useState, type PointerEvent, type WheelEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, Spinner, toastSuccess } from '@/shared/ui';

import {
  buttonPress,
  containedBox,
  framePoint,
  keyCommands,
  textCommands,
  wheelCommand,
  type PhoneButton,
  type ScreenMode,
} from '../model/commands';
import { useScreenStream } from '../model/useScreenStream';

const BUTTONS: PhoneButton[] = ['back', 'home', 'recents'];

// The screen keeps a phone's size and shape from the start — while it connects, and
// when it cannot open — and the stream is fitted into it (`object-fit: contain`).
// As tall as the dialog allows once its header, the phone's buttons and the hint
// have their room (the dialog itself is at most 88dvh tall).
const SCREEN_FRAME = { height: 'calc(88dvh - 240px)', aspectRatio: '9 / 19.5' } as const;

// A phone's live screen. `view` only watches (a warming stint is never disturbed);
// `control` also passes the mouse, the keyboard and the clipboard to the phone.
// Mounted = streaming: closing it closes the socket and the phone stops encoding.
export function PhoneScreen({ phoneId, mode }: { phoneId: string; mode: ScreenMode }) {
  const { t } = useTranslation();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const pressed = useRef(false);
  const [copied, setCopied] = useState(false);

  const onClipboard = useCallback(
    (text: string) => {
      void navigator.clipboard
        ?.writeText(text)
        .then(() => toastSuccess(t('phoneScreen.copied')))
        .catch(() => undefined);
      setCopied(true);
    },
    [t],
  );
  const { state, send } = useScreenStream(phoneId, mode, canvasRef, onClipboard);
  const interactive = state.phase === 'live' && state.controllable && state.size !== null;

  const point = (event: { clientX: number; clientY: number }) => {
    const canvas = canvasRef.current;
    if (!canvas || !state.size) return null;
    const shown = containedBox(canvas.getBoundingClientRect(), state.size);
    return framePoint(event.clientX, event.clientY, shown, state.size);
  };

  const onPointerDown = (event: PointerEvent<HTMLCanvasElement>) => {
    const at = point(event);
    if (!interactive || !at || event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    event.currentTarget.focus();
    pressed.current = true;
    send([{ type: 'touch', action: 'down', ...at }]);
  };
  const onPointerMove = (event: PointerEvent<HTMLCanvasElement>) => {
    const at = point(event);
    if (!interactive || !pressed.current || !at) return;
    send([{ type: 'touch', action: 'move', ...at }]);
  };
  const onPointerUp = (event: PointerEvent<HTMLCanvasElement>) => {
    if (!pressed.current) return;
    pressed.current = false;
    const canvas = canvasRef.current;
    const size = state.size;
    if (!canvas || !size) return;
    const box = containedBox(canvas.getBoundingClientRect(), size);
    // A finger lifted outside the canvas lifts at the nearest edge.
    const x = Math.min(Math.max(event.clientX, box.left), box.left + box.width);
    const y = Math.min(Math.max(event.clientY, box.top), box.top + box.height);
    const at = framePoint(x, y, box, size);
    if (at) send([{ type: 'touch', action: 'up', ...at }]);
  };
  const onWheel = (event: WheelEvent<HTMLCanvasElement>) => {
    const at = point(event);
    if (!interactive || !at) return;
    send([wheelCommand(at, event.deltaX, event.deltaY)]);
  };

  return (
    <div className="flex flex-col items-center gap-3">
      <div style={SCREEN_FRAME} className="relative max-w-full rounded-lg bg-term p-2">
        <canvas
          ref={canvasRef}
          tabIndex={interactive ? 0 : -1}
          aria-label={t(`phoneScreen.canvas.${mode}`)}
          className="block h-full w-full touch-none rounded-md object-contain outline-none focus-visible:shadow-focus"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onWheel={onWheel}
          onContextMenu={(event) => event.preventDefault()}
          onKeyDown={(event) => {
            if (!interactive) return;
            const commands = keyCommands(event);
            if (!commands) return;
            event.preventDefault();
            send(commands);
          }}
          onPaste={(event) => {
            if (!interactive) return;
            event.preventDefault();
            send(textCommands(event.clipboardData.getData('text')));
          }}
        />
        {state.phase === 'connecting' && (
          <div
            role="status"
            className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-on-fill"
          >
            <Spinner tone="onAction" />
            <span className="type-small text-on-fill">{t('phoneScreen.connecting')}</span>
          </div>
        )}
        {/* Why there is no picture, written on the dark screen itself, the way a phone
            shows its own messages: nothing below the screen moves when it appears. */}
        {(state.phase === 'unsupported' || (state.phase === 'closed' && state.reason)) && (
          <div
            role="status"
            className="absolute inset-0 flex items-center justify-center p-6 text-center"
          >
            <span className="type-body text-on-fill">
              {state.phase === 'unsupported'
                ? t('phoneScreen.unsupported')
                : t(`phoneScreen.closed.${state.reason ?? 'screen_lost'}`)}
            </span>
          </div>
        )}
      </div>

      {mode === 'control' && (
        <div className="flex flex-wrap items-center justify-center gap-2">
          {BUTTONS.map((button) => (
            <Button
              key={button}
              size="sm"
              disabled={!interactive}
              onClick={() => send(buttonPress(button))}
            >
              {t(`phoneScreen.button.${button}`)}
            </Button>
          ))}
          <Button
            size="sm"
            variant="ghost"
            disabled={!interactive}
            onClick={() => send([{ type: 'clipboard_get' }])}
          >
            {t(copied ? 'phoneScreen.copyAgain' : 'phoneScreen.copy')}
          </Button>
        </div>
      )}
      {mode === 'control' && <p className="type-small">{t('phoneScreen.hint')}</p>}
    </div>
  );
}

// The same screen before there is a phone to stream: a pod still starting or Android
// still booting. Nothing connects; the frame holds its place and says what it waits for.
export function PhoneScreenWaiting({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center gap-3">
      <div
        style={SCREEN_FRAME}
        role="status"
        className="flex max-w-full flex-col items-center justify-center gap-2 rounded-lg bg-term p-6 text-center"
      >
        <Spinner tone="onAction" />
        <span className="type-small text-on-fill">{label}</span>
      </div>
    </div>
  );
}
