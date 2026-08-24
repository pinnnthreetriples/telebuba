import { type ReactNode } from 'react';

import { CollapsibleCard, Icon } from '@/shared/ui';

// The accordion preset shared by every AccountEdit section, plus the profile
// modal's dashed add-tile. Internal to the slice (not re-exported from index).
// Styles/types live in ./_styles.

// Dashed "add" tile used by the profile modal's photo and stories grids.
export function DashedAdd({
  ratio,
  label,
  onClick,
  disabled = false,
}: {
  ratio: string;
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      style={{ aspectRatio: ratio }}
      className="flex flex-col items-center justify-center gap-sm rounded-lg border-[1.5px] border-dashed border-line-strong bg-white text-body font-medium text-ink-muted disabled:opacity-60"
    >
      <Icon name="plus" size={20} />
      {label}
    </button>
  );
}

export function Spinner({ size }: { size: number }) {
  return (
    <span
      className="tb-spin inline-block rounded-full border-2 border-line-strong border-t-primary"
      style={{ width: size, height: size }}
    />
  );
}

// The account-edit cards' accordion: the shared CollapsibleCard with this
// slice's header padding and title style. It used to be a second implementation
// that omitted the `--mh` measurement (so a tall body was clipped at the CSS
// fallback 600px with no scrollbar) and the aria-expanded/hidden handling (so
// every collapsed card's controls stayed in the tab order).
// `right` renders an action between the title and chevron (the signals @SpamBot check).
export function Section({
  title,
  icon,
  right,
  bodyClassName = 'px-xl pb-xl',
  onOpenChange,
  children,
}: {
  title: string;
  icon?: ReactNode;
  right?: ReactNode;
  bodyClassName?: string;
  // Passed through for the 2FA card, whose one-time plaintext must not survive
  // a collapse (a collapsed body is hidden, not unmounted).
  onOpenChange?: (open: boolean) => void;
  children: ReactNode;
}) {
  return (
    <CollapsibleCard
      label={title}
      trailing={right}
      onOpenChange={onOpenChange}
      wrapperClassName="self-start rounded-card border border-line bg-white"
      headerClassName="px-xl py-lg"
      bodyClassName={bodyClassName}
      header={
        <span className="flex items-center gap-sm text-lead font-semibold text-ink">
          {title}
          {icon}
        </span>
      }
    >
      {children}
    </CollapsibleCard>
  );
}
