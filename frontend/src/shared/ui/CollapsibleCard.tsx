import { type ReactNode, useLayoutEffect, useRef, useState } from 'react';

// The design's collapsible accordion card: a header row (free-form content +
// chevron) over a max-height-collapsing body. Used across the account-edit,
// warming and neurocomment screens, which all share this pattern in the design.
function Chevron({ open }: { open: boolean }) {
  return (
    <span
      className={`flex text-ink-subtle transition-transform duration-[420ms] [transition-timing-function:cubic-bezier(.34,1.45,.6,1)] ${open ? 'rotate-180' : ''}`}
    >
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      >
        <path d="m6 9 6 6 6-6" />
      </svg>
    </span>
  );
}

export function CollapsibleCard({
  header,
  trailing,
  label,
  defaultOpen = false,
  wrapperClassName = 'rounded-2xl border border-line bg-white',
  headerClassName = 'px-4 py-[14px]',
  bodyClassName = 'px-4 pb-4',
  children,
}: {
  header: ReactNode;
  trailing?: ReactNode;
  label?: string;
  defaultOpen?: boolean;
  wrapperClassName?: string;
  headerClassName?: string;
  bodyClassName?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const collapseRef = useRef<HTMLDivElement>(null);
  // Drop the max-height cap once the open transition settles, so content is
  // never clipped (the .tb-collapse `var(--mh)` cap is only for the animation).
  const [settled, setSettled] = useState(defaultOpen);
  const toggle = () => {
    setSettled(false);
    setOpen((value) => !value);
  };

  // Drive the transition from the real content height so tall content (>600px)
  // isn't cut by the CSS fallback cap.
  useLayoutEffect(() => {
    const el = collapseRef.current;
    if (!el) return;
    if (open) {
      el.style.setProperty('--mh', `${String(el.scrollHeight)}px`);
    } else {
      el.style.removeProperty('--mh');
    }
  }, [open, children]);

  return (
    <div className={`overflow-hidden ${wrapperClassName}`}>
      <div className={`flex items-center gap-[10px] ${headerClassName}`}>
        <button
          type="button"
          onClick={toggle}
          className="flex min-w-0 flex-1 items-center gap-[9px] text-left"
        >
          {header}
        </button>
        {trailing}
        <button
          type="button"
          onClick={toggle}
          aria-label={label}
          className="flex shrink-0 items-center"
        >
          <Chevron open={open} />
        </button>
      </div>
      <div
        ref={collapseRef}
        className={`tb-collapse ${open ? 'tb-open' : ''} ${open && settled ? 'tb-settled' : ''}`}
        onTransitionEnd={(event) => {
          if (event.propertyName === 'max-height' && open) setSettled(true);
        }}
      >
        <div className={bodyClassName}>{children}</div>
      </div>
    </div>
  );
}
