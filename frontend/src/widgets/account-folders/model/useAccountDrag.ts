import { type PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from 'react';

// Rows are dragged by their grip onto a folder tab. A tab opts in with
// `data-folder-drop="<folder id>"`; anything else under the pointer is "no target".
export const DROP_ATTRIBUTE = 'data-folder-drop';

// Below this many pixels a press is a click on the grip, not a drag.
const THRESHOLD_PX = 5;

export interface AccountDrag {
  ids: string[];
  x: number;
  y: number;
  folderId: string | null;
}

interface Pending {
  pointerId: number;
  ids: string[];
  startX: number;
  startY: number;
  started: boolean;
  folderId: string | null;
}

// The folder tab under a point. `elementFromPoint` first — on touch the pointer is
// captured by the grip, so the event's own target never leaves it — then the target.
function dropTargetAt(event: PointerEvent): string | null {
  const hit = document.elementFromPoint(event.clientX, event.clientY) ?? event.target;
  if (!(hit instanceof Element)) return null;
  return hit.closest(`[${DROP_ATTRIBUTE}]`)?.getAttribute(DROP_ATTRIBUTE) ?? null;
}

// Pointer-driven, not HTML5 drag-and-drop: the native ghost is a screenshot of the row,
// and the design's ghost is a pill of avatars that changes when it is over a folder.
// Dragging a selected row drags the whole selection; an unselected row goes alone.
// Escape cancels.
export function useAccountDrag(onDrop: (folderId: string, accountIds: string[]) => void) {
  const [drag, setDrag] = useState<AccountDrag | null>(null);
  const pending = useRef<Pending | null>(null);
  const dropRef = useRef(onDrop);
  useEffect(() => {
    dropRef.current = onDrop;
  }, [onDrop]);

  useEffect(() => {
    const end = () => {
      pending.current = null;
      setDrag(null);
    };
    const move = (event: PointerEvent) => {
      const current = pending.current;
      if (!current || current.pointerId !== event.pointerId) return;
      const distance = Math.hypot(event.clientX - current.startX, event.clientY - current.startY);
      if (!current.started && distance < THRESHOLD_PX) return;
      current.started = true;
      current.folderId = dropTargetAt(event);
      setDrag({
        ids: current.ids,
        x: event.clientX,
        y: event.clientY,
        folderId: current.folderId,
      });
    };
    const up = (event: PointerEvent) => {
      const current = pending.current;
      if (!current || current.pointerId !== event.pointerId) return;
      end();
      if (current.started && current.folderId) dropRef.current(current.folderId, current.ids);
    };
    const key = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || !pending.current) return;
      event.preventDefault();
      event.stopPropagation();
      end();
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
    document.addEventListener('pointercancel', end);
    document.addEventListener('keydown', key, true);
    return () => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
      document.removeEventListener('pointercancel', end);
      document.removeEventListener('keydown', key, true);
    };
  }, []);

  const startDrag = (
    event: ReactPointerEvent<HTMLElement>,
    accountId: string,
    selected: ReadonlySet<string>,
  ) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    pending.current = {
      pointerId: event.pointerId,
      ids: selected.has(accountId) ? [...selected] : [accountId],
      startX: event.clientX,
      startY: event.clientY,
      started: false,
      folderId: null,
    };
  };

  return { drag, startDrag };
}
