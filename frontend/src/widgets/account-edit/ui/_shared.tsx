import { useState, type ReactNode } from 'react';

// The accordion primitives shared by every AccountEdit section. Internal to the
// slice (not re-exported from index). Styles/types live in ./_styles.

export function Spinner({ size }: { size: number }) {
  return (
    <span
      className="tb-spin inline-block rounded-full border-2 border-[#c8c6c2] border-t-primary"
      style={{ width: size, height: size }}
    />
  );
}

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

// A design accordion card: header (title + chevron) + max-height-collapsing body.
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
  const [open, setOpen] = useState(false);
  const toggle = () => {
    setOpen((value) => !value);
  };
  const heading = (
    <span className="flex items-center gap-[7px] text-[13px] font-semibold text-ink">
      {title}
      {icon}
    </span>
  );
  return (
    <div className="self-start overflow-hidden rounded-2xl border border-line bg-white">
      {right ? (
        <div className="flex items-center gap-[10px] px-5 py-4">
          <button
            type="button"
            onClick={toggle}
            className="flex flex-1 items-center gap-[10px] text-left"
          >
            {heading}
          </button>
          {right}
          <button
            type="button"
            onClick={toggle}
            aria-label={title}
            className="flex shrink-0 items-center"
          >
            <Chevron open={open} />
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={toggle}
          className="flex w-full items-center justify-between gap-[10px] px-5 py-4 text-left"
        >
          {heading}
          <Chevron open={open} />
        </button>
      )}
      <div className={`tb-collapse ${open ? 'tb-open' : ''}`}>
        <div className={bodyClassName}>{children}</div>
      </div>
    </div>
  );
}
