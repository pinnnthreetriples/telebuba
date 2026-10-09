import type { PointerEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { Icon } from '@/shared/ui';

import { ROW_CHECKBOX } from './FolderTabStrip';

// A row's checkbox and drag grip, before its avatar. Neither opens the row: selecting
// is the checkbox's job only, and the row click stays "open the account".
export function RowPick({
  name,
  selected,
  onToggle,
  onGripDown,
}: {
  name: string;
  selected: boolean;
  onToggle: () => void;
  onGripDown: (event: PointerEvent<HTMLElement>) => void;
}) {
  const { t } = useTranslation();
  return (
    // Stops the row's own click and key handling: picking a row must not open it.
    <span
      className="inline-flex shrink-0 items-center gap-1"
      onClick={(event) => {
        event.stopPropagation();
      }}
      onKeyDown={(event) => {
        event.stopPropagation();
      }}
    >
      <label className="flex items-center p-1">
        <input
          type="checkbox"
          checked={selected}
          aria-label={t('accounts.folders.select', { name })}
          onChange={onToggle}
          className={ROW_CHECKBOX}
        />
      </label>
      <button
        type="button"
        aria-label={t('accounts.folders.drag', { name })}
        className="cursor-grab touch-none p-1 text-content-subtle"
        onPointerDown={onGripDown}
      >
        <Icon name="grip-vertical" size={14} />
      </button>
    </span>
  );
}
