import { type ReactNode } from 'react';

import { CollapsibleCard } from '@/shared/ui';

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
      className="flex flex-col items-center justify-center gap-[6px] rounded-[12px] border-[1.5px] border-dashed border-[#d2d0cc] bg-white text-[12px] font-medium text-ink-muted disabled:opacity-60"
    >
      <svg
        width="20"
        height="20"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
      >
        <path d="M12 5v14M5 12h14" />
      </svg>
      {label}
    </button>
  );
}

export function Spinner({ size }: { size: number }) {
  return (
    <span
      className="tb-spin inline-block rounded-full border-2 border-[#c8c6c2] border-t-primary"
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
  bodyClassName = 'px-5 pb-[18px]',
  children,
}: {
  title: string;
  icon?: ReactNode;
  right?: ReactNode;
  bodyClassName?: string;
  children: ReactNode;
}) {
  return (
    <CollapsibleCard
      label={title}
      trailing={right}
      wrapperClassName="self-start rounded-2xl border border-line bg-white"
      headerClassName="px-5 py-4"
      bodyClassName={bodyClassName}
      header={
        <span className="flex items-center gap-[7px] text-[13px] font-semibold text-ink">
          {title}
          {icon}
        </span>
      }
    >
      {children}
    </CollapsibleCard>
  );
}
