import { type DragEvent, useState } from 'react';

import { ACCOUNT_DRAG_TYPE } from './folders';

// Зона броска готового аккаунта: подсвечивается, пока над ней держат карточку.
export function useAccountDrop(onDrop: ((accountId: string) => void) | undefined) {
  const [over, setOver] = useState(false);
  const accepts = (event: DragEvent) =>
    onDrop !== undefined && event.dataTransfer.types.includes(ACCOUNT_DRAG_TYPE);
  return {
    over,
    handlers: {
      onDragOver: (event: DragEvent) => {
        if (!accepts(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
        setOver(true);
      },
      onDragLeave: () => {
        setOver(false);
      },
      onDrop: (event: DragEvent) => {
        if (!accepts(event)) return;
        event.preventDefault();
        setOver(false);
        const id = event.dataTransfer.getData(ACCOUNT_DRAG_TYPE);
        if (id) onDrop?.(id);
      },
    },
  };
}
