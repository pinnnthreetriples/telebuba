import type { ReactNode } from 'react';

import {
  selectableCardShell,
  selectableCardTarget,
  selectableCardContent,
  selectableCardTitle,
  selectableCardMeta,
  selectableCardActions,
} from '@/shared/design-system';

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
        <div className={selectableCardShell(selected)}>
          <button
            type="button"
            aria-pressed={selected}
            aria-label={name}
            onClick={onSelect}
            className={selectableCardTarget()}
          />
          <div className={selectableCardContent()}>
            <div className="min-w-0 flex-1">
              <div className={selectableCardTitle()}>{name}</div>
              <div className={selectableCardMeta()}>{meta}</div>
            </div>
            <div className={selectableCardActions()}>
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
