import type { ReactNode } from 'react';

import { cn } from '@/shared/lib';
import { Button } from '@/shared/ui';

// One choice in a filter group: a small round toggle, selected = info tint. A group is a
// radiogroup, so each pill is a radio. The box is `Button`'s — a pill drawn by hand
// outside the design system would re-decide its height, padding and focus ring.
export function FilterPill({
  selected,
  onSelect,
  title,
  children,
}: {
  selected: boolean;
  onSelect: () => void;
  title?: string;
  children: ReactNode;
}) {
  return (
    <Button
      size="sm"
      role="radio"
      aria-checked={selected}
      title={title}
      aria-label={title}
      onClick={onSelect}
      className={cn(
        'gap-2',
        selected
          ? 'border-info-line bg-info-tint text-info-strong hover:border-info-line'
          : 'text-content-secondary hover:text-content-primary',
      )}
    >
      {children}
    </Button>
  );
}

export function Flag({ code }: { code: string }) {
  return (
    <span
      aria-hidden
      className={`fi fi-${code.toLowerCase()} h-flag w-flag shrink-0 rounded-[2px] shadow-ring`}
    />
  );
}
