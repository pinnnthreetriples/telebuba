import { useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { cn } from '@/shared/lib/cn';
import { Icon } from '@/shared/ui';

import {
  clampCenter,
  clampScale,
  distance,
  normalizeAngle,
  nudge,
  PLATE_HEIGHT_RATIO,
  ROTATE_KEY_STEP,
  rotationFromHandle,
  scaleFromDrag,
  SCALE_KEY_STEP,
  type Point,
  type Size,
} from '../model/geometry';
import { labelProblem } from '../model/label';
import type { LinkSticker, LinkStickerStyle } from '../model/types';

// The plate as the server draws it: a stadium whose height is 6% of the canvas height
// (times the sticker's scale), a link glyph left of the label, padding proportional to
// the height. Everything is sized in container-query units of the canvas, so the plate
// keeps its proportions at any preview width; `scale` is a transform on the plate.
const STICKER_STYLE_CLASS: Record<LinkStickerStyle, string> = {
  white: 'bg-surface-card text-content-primary',
  dark: 'bg-term text-on-fill',
  glass: 'bg-surface-card/30 text-on-fill',
  accent: 'bg-action-primary text-on-fill',
};

const HEIGHT = PLATE_HEIGHT_RATIO * 100;
// Padding and glyph as shares of the plate height: the server's own ratios, so the
// preview is as wide as the plate that gets drawn (`_story_stickers.py`).
const PAD_RATIO = 0.4;
const ICON_RATIO = 0.42;

type Drag =
  | { mode: 'move'; start: Point; origin: Point; box: Size }
  | { mode: 'scale'; startDistance: number; startScale: number; box: Size; center: Point }
  | { mode: 'rotate' };

export interface CanvasRect extends Size {
  left: number;
  top: number;
}

export function StickerPill({
  sticker,
  selected,
  disabled,
  getCanvasRect,
  onChange,
  onSelect,
  onRemove,
}: {
  sticker: LinkSticker;
  selected: boolean;
  disabled: boolean;
  getCanvasRect: () => CanvasRect;
  onChange: (patch: Partial<LinkSticker>) => void;
  onSelect: () => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const plateRef = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [labelInvalid, setLabelInvalid] = useState(false);

  const centerPx = (rect: CanvasRect): Point => ({
    x: rect.left + (sticker.x / 100) * rect.width,
    y: rect.top + (sticker.y / 100) * rect.height,
  });
  const boxNow = (): Size => {
    const box = plateRef.current?.getBoundingClientRect();
    return { width: box?.width ?? 0, height: box?.height ?? 0 };
  };
  const capture = (event: PointerEvent<HTMLElement>) => {
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };

  const begin = (event: PointerEvent<HTMLDivElement>) => {
    if (disabled || editing !== null) return;
    capture(event);
    onSelect();
    event.currentTarget.focus();
    drag.current = {
      mode: 'move',
      start: { x: event.clientX, y: event.clientY },
      origin: { x: sticker.x, y: sticker.y },
      box: boxNow(),
    };
  };

  const beginScale = (event: PointerEvent<HTMLSpanElement>) => {
    if (disabled) return;
    event.stopPropagation();
    capture(event);
    onSelect();
    const rect = getCanvasRect();
    const center = centerPx(rect);
    drag.current = {
      mode: 'scale',
      startDistance: distance({ x: event.clientX, y: event.clientY }, center),
      startScale: sticker.scale,
      box: boxNow(),
      center: { x: sticker.x, y: sticker.y },
    };
  };

  const beginRotate = (event: PointerEvent<HTMLSpanElement>) => {
    if (disabled) return;
    event.stopPropagation();
    capture(event);
    onSelect();
    drag.current = { mode: 'rotate' };
  };

  const move = (event: PointerEvent<HTMLDivElement>) => {
    const current = drag.current;
    if (!current) return;
    const rect = getCanvasRect();
    const pointer = { x: event.clientX, y: event.clientY };
    if (current.mode === 'move') {
      const next = clampCenter(
        {
          x: current.origin.x + ((pointer.x - current.start.x) / rect.width) * 100,
          y: current.origin.y + ((pointer.y - current.start.y) / rect.height) * 100,
        },
        current.box,
        rect,
      );
      onChange(next);
    } else if (current.mode === 'scale') {
      const scale = scaleFromDrag(
        current.startScale,
        current.startDistance,
        distance(pointer, centerPx(rect)),
      );
      const ratio = current.startScale > 0 ? scale / current.startScale : 1;
      const box = { width: current.box.width * ratio, height: current.box.height * ratio };
      onChange({ scale, ...clampCenter(current.center, box, rect) });
    } else {
      onChange({ rotation: rotationFromHandle(centerPx(rect), pointer, event.shiftKey) });
    }
  };

  const end = () => {
    drag.current = null;
  };

  const keyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (disabled || editing !== null) return;
    const { key } = event;
    if (key === 'Delete' || key === 'Backspace') {
      event.preventDefault();
      onRemove();
      return;
    }
    if (key === 'Enter' || key === 'F2') {
      event.preventDefault();
      setEditing(sticker.label);
      return;
    }
    const moved = nudge({ x: sticker.x, y: sticker.y }, key, event.shiftKey);
    if (moved) {
      event.preventDefault();
      const rect = getCanvasRect();
      onChange(clampCenter(moved, boxNow(), rect));
      return;
    }
    if (key === '+' || key === '=' || key === '-') {
      event.preventDefault();
      onChange({
        scale: clampScale(sticker.scale + (key === '-' ? -SCALE_KEY_STEP : SCALE_KEY_STEP)),
      });
    } else if (key === '[' || key === ']') {
      event.preventDefault();
      const turn = key === '[' ? -ROTATE_KEY_STEP : ROTATE_KEY_STEP;
      onChange({ rotation: normalizeAngle(sticker.rotation + turn) });
    }
  };

  const commitLabel = (raw: string) => {
    const label = raw.trim();
    if (labelProblem(label) !== null) {
      setLabelInvalid(true);
      return false;
    }
    setEditing(null);
    setLabelInvalid(false);
    onChange({ label });
    return true;
  };

  const text = sticker.label === '' ? t('storyComposer.stickers.labelPlaceholder') : sticker.label;

  return (
    <div
      ref={plateRef}
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-pressed={selected}
      aria-label={t('storyComposer.canvas.sticker', { label: sticker.label })}
      aria-disabled={disabled || undefined}
      data-sticker-id={sticker.id}
      className={cn(
        'absolute flex touch-none select-none items-center whitespace-nowrap rounded-full font-medium',
        STICKER_STYLE_CLASS[sticker.style],
        selected && 'outline outline-action-primary',
        disabled ? 'cursor-default' : 'cursor-grab',
      )}
      style={{
        left: `${String(sticker.x)}%`,
        top: `${String(sticker.y)}%`,
        height: `${String(HEIGHT)}cqh`,
        paddingInline: `${String(HEIGHT * PAD_RATIO)}cqh`,
        gap: `${String(HEIGHT * 0.25)}cqh`,
        fontSize: `${String(HEIGHT * 0.45)}cqh`,
        transform: `translate(-50%, -50%) rotate(${String(sticker.rotation)}deg) scale(${String(sticker.scale)})`,
      }}
      onPointerDown={begin}
      onPointerMove={move}
      onPointerUp={end}
      onPointerCancel={end}
      onDoubleClick={() => {
        if (!disabled) setEditing(sticker.label);
      }}
      onKeyDown={keyDown}
    >
      <span
        aria-hidden="true"
        className="shrink-0"
        style={{
          width: `${String(HEIGHT * ICON_RATIO)}cqh`,
          height: `${String(HEIGHT * ICON_RATIO)}cqh`,
        }}
      >
        <Icon name="link" size={12} className="h-full w-full" />
      </span>
      {editing !== null ? (
        <input
          autoFocus
          value={editing}
          aria-label={t('storyComposer.canvas.editLabel')}
          aria-invalid={labelInvalid || undefined}
          className="min-w-0 bg-transparent font-medium text-current outline-hidden"
          style={{ width: `${String(Math.max(editing.length, 4))}ch`, fontSize: 'inherit' }}
          onChange={(event) => {
            setEditing(event.target.value);
            setLabelInvalid(false);
          }}
          onPointerDown={(event) => {
            event.stopPropagation();
          }}
          onBlur={() => {
            if (!commitLabel(editing)) {
              setEditing(null);
              setLabelInvalid(false);
            }
          }}
          onKeyDown={(event) => {
            event.stopPropagation();
            if (event.key === 'Enter') {
              event.preventDefault();
              commitLabel(editing);
            } else if (event.key === 'Escape') {
              event.preventDefault();
              setEditing(null);
              setLabelInvalid(false);
            }
          }}
        />
      ) : (
        <span>{text}</span>
      )}
      {selected && !disabled ? (
        <>
          <span
            aria-hidden="true"
            data-handle="rotate"
            title={t('storyComposer.canvas.rotateHandle')}
            className="absolute size-node cursor-grab rounded-full border border-surface-card bg-action-primary"
            style={{ left: '50%', top: '-100%', transform: 'translateX(-50%)' }}
            onPointerDown={beginRotate}
          />
          <span
            aria-hidden="true"
            data-handle="scale"
            title={t('storyComposer.canvas.scaleHandle')}
            className="absolute size-node cursor-nwse-resize rounded-full border border-surface-card bg-action-primary"
            style={{ right: '0%', bottom: '0%', transform: 'translate(50%, 50%)' }}
            onPointerDown={beginScale}
          />
        </>
      ) : null}
    </div>
  );
}
