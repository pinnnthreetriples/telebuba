import type { ReactNode } from 'react';

import { FOCUS_RING } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

import { Icon } from './Icon';
import { IconButton } from './IconButton';
import { SurfHover } from './SurfHover';

/** A selectable card with an independent, revealable action layer. */
export function SelectableCard({
  surfaceId,
  name,
  meta,
  status,
  selected,
  actionsOpen,
  actionsLabel,
  actions,
  onSelect,
  onToggleActions,
}: {
  surfaceId: string;
  name: string;
  meta: string;
  status: ReactNode;
  selected: boolean;
  actionsOpen: boolean;
  actionsLabel: string;
  actions: ReactNode;
  onSelect: () => void;
  onToggleActions: () => void;
}) {
  return (
    <SurfHover
      surfaceId={surfaceId}
      open={actionsOpen}
      actions={actions}
      surface={
        <div
          className={`relative rounded-lg border p-lg ${selected ? 'border-action-primary bg-info-tint' : 'border-line bg-surface-card'}`}
        >
          <button
            type="button"
            aria-pressed={selected}
            aria-label={name}
            onClick={onSelect}
            className={cn('absolute inset-0 cursor-pointer rounded-lg', FOCUS_RING)}
          />
          <div className="pointer-events-none flex justify-between gap-md">
            <div className="min-w-0 flex-1">
              <div className="mb-tight truncate type-h3">{name}</div>
              <div className="truncate type-small">{meta}</div>
            </div>
            <div className="flex shrink-0 flex-col items-end gap-sm">
              {status}
              <span className="pointer-events-auto relative">
                <IconButton
                  size="sm"
                  tone="primary"
                  aria-controls={surfaceId}
                  aria-expanded={actionsOpen}
                  title={actionsLabel}
                  aria-label={actionsLabel}
                  onClick={onToggleActions}
                >
                  <Icon name="gear" size={14} />
                </IconButton>
              </span>
            </div>
          </div>
        </div>
      }
    />
  );
}
