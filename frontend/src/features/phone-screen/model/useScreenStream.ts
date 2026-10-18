import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';

import {
  closeReason,
  parseScreenMessage,
  screenSocketUrl,
  type ScreenCommand,
  type ScreenMode,
} from './commands';
import type { FrameSize, ScreenDecoder } from './decoder';

type DecoderModule = typeof import('./decoder');

export type ScreenPhase = 'connecting' | 'live' | 'closed' | 'unsupported';

export type ScreenState = {
  phase: ScreenPhase;
  // Why it closed: a `ScreenCloseCode`, a refusal code, or `screen_lost`.
  reason: string | null;
  controllable: boolean;
  size: FrameSize | null;
};

const INITIAL: ScreenState = { phase: 'connecting', reason: null, controllable: false, size: null };

// One phone screen over one WebSocket, for as long as the component is mounted.
// Unmounting closes the socket, and the backend stops scrcpy on the phone with it:
// nothing encodes while nobody watches.
export function useScreenStream(
  phoneId: string,
  mode: ScreenMode,
  canvasRef: RefObject<HTMLCanvasElement | null>,
  onClipboard: (text: string) => void,
) {
  const [state, setState] = useState<ScreenState>(INITIAL);
  const socketRef = useRef<WebSocket | null>(null);
  const clipboardRef = useRef(onClipboard);
  useEffect(() => {
    clipboardRef.current = onClipboard;
  }, [onClipboard]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    setState(INITIAL);
    let disposed = false;
    let socket: WebSocket | null = null;
    let decoder: ScreenDecoder | null = null;
    let feed: ReadableStreamDefaultController<Uint8Array> | null = null;
    const endFeed = () => {
      try {
        feed?.close();
      } catch {
        // already closed or errored
      }
    };

    const connect = ({ canDecodeScreen, decodeScreen }: DecoderModule) => {
      if (disposed) return;
      if (!canDecodeScreen()) {
        setState({ ...INITIAL, phase: 'unsupported' });
        return;
      }
      const video = new ReadableStream<Uint8Array>({
        start: (controller) => {
          feed = controller;
        },
      });
      const opened = new WebSocket(screenSocketUrl(phoneId, mode, window.location));
      opened.binaryType = 'arraybuffer';
      socket = opened;
      socketRef.current = opened;

      opened.onmessage = (event: MessageEvent<ArrayBuffer | string>) => {
        if (typeof event.data !== 'string') {
          feed?.enqueue(new Uint8Array(event.data));
          return;
        }
        const message = parseScreenMessage(event.data);
        if (message?.type === 'ready') {
          setState((prev) => ({ ...prev, phase: 'live', controllable: message.controllable }));
        } else if (message?.type === 'clipboard') {
          clipboardRef.current(message.text);
        }
      };
      opened.onclose = (event) => {
        endFeed();
        setState((prev) => ({
          ...prev,
          phase: 'closed',
          reason: closeReason(event.code, event.reason),
        }));
      };

      void decodeScreen(video, canvas, (size) => {
        if (!disposed) setState((prev) => ({ ...prev, size }));
      })
        .then((started) => {
          if (disposed) started.dispose();
          else decoder = started;
        })
        .catch(() => undefined); // the stream ended before its header: the close says why
    };

    // The decoder (and @yume-chan with it) loads only when a screen opens: the pages
    // that merely offer one — the warming board — do not carry it.
    void import('./decoder').then(connect);

    return () => {
      disposed = true;
      socketRef.current = null;
      if (socket) {
        socket.onmessage = null;
        socket.onclose = null;
        socket.close(1000);
      }
      endFeed(); // a decoder still waiting for the codec header gives up
      decoder?.dispose();
    };
  }, [phoneId, mode, canvasRef]);

  const send = useCallback((commands: ScreenCommand[]) => {
    const socket = socketRef.current;
    if (socket?.readyState !== WebSocket.OPEN) return;
    for (const command of commands) socket.send(JSON.stringify(command));
  }, []);

  return { state, send };
}
