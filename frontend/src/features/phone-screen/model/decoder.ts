import { ScrcpyOptions3_3_1 } from '@yume-chan/scrcpy';
import {
  BitmapVideoFrameRenderer,
  WebCodecsVideoDecoder,
  WebGLVideoFrameRenderer,
} from '@yume-chan/scrcpy-decoder-webcodecs';

// The video bytes from the socket, decoded in hardware (WebCodecs) onto a canvas.
//
// The options must describe the server the backend starts (`core/emulator/scrcpy.py`):
// scrcpy-server 3.3.1, h264, codec header on, no device-name header, frame headers on.
// The backend reads the connection's dummy byte itself, so the stream starts at the
// codec header. Nothing here talks to the phone: commands go as JSON over the socket.

export type FrameSize = { width: number; height: number };

export type ScreenDecoder = { dispose: () => void };

// `@yume-chan/stream-extra` re-exports the platform's ReadableStream with its own
// typing (`closed: Promise<undefined>` where lib.dom has `Promise<void>`): the same
// object at runtime, two declarations at compile time.
type ScrcpyReadable = Parameters<ScrcpyOptions3_3_1<true>['parseVideoStreamMetadata']>[0];

export function canDecodeScreen(): boolean {
  return WebCodecsVideoDecoder.isSupported;
}

export async function decodeScreen(
  video: ReadableStream<Uint8Array>,
  canvas: HTMLCanvasElement,
  onSize: (size: FrameSize) => void,
): Promise<ScreenDecoder> {
  const options = new ScrcpyOptions3_3_1({
    audio: false,
    control: false,
    sendDeviceMeta: false,
    videoCodec: 'h264',
  });
  const { metadata, stream } = await options.parseVideoStreamMetadata(
    video as unknown as ScrcpyReadable,
  );
  const renderer = WebGLVideoFrameRenderer.isSupported
    ? new WebGLVideoFrameRenderer(canvas)
    : new BitmapVideoFrameRenderer(canvas);
  const decoder = new WebCodecsVideoDecoder({ codec: metadata.codec, renderer });
  decoder.sizeChanged(onSize);
  // A closed socket ends the stream, and a disposed decoder rejects the pipe: both
  // are the screen ending, which the socket's close already reports.
  void stream
    .pipeThrough(options.createMediaStreamTransformer())
    .pipeTo(decoder.writable)
    .catch(() => undefined);
  return { dispose: () => decoder.dispose() };
}
