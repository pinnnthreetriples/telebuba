import { useRef } from 'react';
import { useTranslation } from 'react-i18next';

import { CANVAS_ASPECT } from '../model/geometry';
import type { LinkSticker, StoryMedia } from '../model/types';
import { StickerPill, type CanvasRect } from './StickerPill';

const COLLAGE_MAX = 4;

function MediaLayer({ media }: { media: StoryMedia }) {
  if (media.kind === 'video') {
    // The fragment asks for a frame past zero so the poster is not black.
    return (
      <video
        muted
        playsInline
        preload="metadata"
        src={`${media.url}#t=0.1`}
        className="absolute inset-0 h-full w-full object-cover"
      />
    );
  }
  if (media.kind === 'image') {
    return <img src={media.url} alt="" className="absolute inset-0 h-full w-full object-cover" />;
  }
  const urls = media.urls.slice(0, COLLAGE_MAX);
  const single = urls.length <= 1 || media.layout === 'rows';
  return (
    <div
      className="absolute inset-0 grid gap-1"
      style={{ gridTemplateColumns: single ? '1fr' : 'repeat(2, 1fr)' }}
    >
      {(single ? urls.slice(0, 1) : urls).map((url) => (
        <img key={url} src={url} alt="" className="h-full w-full object-cover" />
      ))}
    </div>
  );
}

// A 9:16 preview of the story with its link stickers on top. The stickers are edited
// in place — dragging, the corner handle, the rotate handle, the keyboard — and every
// edit goes out through `onChange`; the canvas holds no copy of them.
export function StoryCanvas({
  media,
  stickers,
  onChange,
  selectedId,
  onSelect,
  disabled = false,
}: {
  media: StoryMedia | null;
  stickers: LinkSticker[];
  onChange: (stickers: LinkSticker[]) => void;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const canvasRef = useRef<HTMLDivElement>(null);

  const getRect = (): CanvasRect => {
    const rect = canvasRef.current?.getBoundingClientRect();
    return {
      left: rect?.left ?? 0,
      top: rect?.top ?? 0,
      width: rect?.width ?? 0,
      height: rect?.height ?? 0,
    };
  };

  return (
    <div
      ref={canvasRef}
      role="group"
      aria-label={t('storyComposer.canvas.label')}
      aria-description={stickers.length > 0 ? t('storyComposer.canvas.hint') : undefined}
      className="relative w-full overflow-hidden rounded-md bg-term"
      style={{ aspectRatio: CANVAS_ASPECT, containerType: 'size' }}
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) onSelect(null);
      }}
    >
      {media ? (
        <MediaLayer media={media} />
      ) : (
        <p className="type-small absolute inset-0 flex items-center justify-center text-term-dim">
          {t('storyComposer.canvas.empty')}
        </p>
      )}
      {stickers.map((sticker) => (
        <StickerPill
          key={sticker.id}
          sticker={sticker}
          selected={sticker.id === selectedId}
          disabled={disabled}
          getCanvasRect={getRect}
          onSelect={() => {
            onSelect(sticker.id);
          }}
          onChange={(patch) => {
            onChange(
              stickers.map((item) => (item.id === sticker.id ? { ...item, ...patch } : item)),
            );
          }}
          onRemove={() => {
            onChange(stickers.filter((item) => item.id !== sticker.id));
            onSelect(null);
          }}
        />
      ))}
    </div>
  );
}
